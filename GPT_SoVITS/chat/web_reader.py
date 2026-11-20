"""将公开网页转换为可按需续读的模型工具结果。"""
from __future__ import annotations

import ipaddress
import socket
import time
from dataclasses import dataclass
from urllib.parse import urljoin, urlsplit, urlunsplit

import requests
from requests.adapters import HTTPAdapter

PAGE_SIZE = 6000
MAX_BYTES = 3 * 1024 * 1024
CLASH_FAKE_IP_RANGE = ipaddress.IPv4Network("198.18.0.0/16")


def _allowed_address(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """允许公网地址和本机 Clash 负责转发的 IPv4 映射地址。"""
    return ip in CLASH_FAKE_IP_RANGE or (ip.is_global and not ip.is_multicast and not ip.is_reserved)


class _PinnedHTTPSAdapter(HTTPAdapter):
    """连接预先验证的公网 IP，仍用原域名进行 SNI 和证书验证。"""

    def __init__(self, hostname: str) -> None:
        """为本次连接固定证书主机名。"""
        self.hostname = hostname
        super().__init__()

    def init_poolmanager(self, connections: int, maxsize: int, block: bool = False, **kwargs: object) -> None:
        """为底层 TLS 连接保留原站身份。"""
        super().init_poolmanager(connections, maxsize, block, server_hostname=self.hostname,
                                 assert_hostname=self.hostname, **kwargs)


@dataclass(frozen=True)
class Page:
    """保存本轮已读取的页面快照。"""

    url: str
    title: str
    content: str


def _fetch(url: str) -> Page:
    """逐跳检查目的地址，限制正文体积并提取静态文本。"""
    deadline = time.monotonic() + 25
    for _ in range(6):
        parts = urlsplit(url)
        host = parts.hostname
        if parts.scheme not in {"http", "https"} or not host or parts.username is not None or parts.password is not None:
            raise ValueError("仅支持不含账号信息的公网 HTTP(S) 链接。")
        port = parts.port or (443 if parts.scheme == "https" else 80)
        addresses = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
        ips = [ipaddress.ip_address(item[4][0]) for item in addresses]
        if not ips or not all(_allowed_address(ip) for ip in ips):
            raise ValueError("链接指向非公网地址。")
        ip = ips[0]
        addr = f"[{ip}]" if ip.version == 6 else str(ip)
        target = urlunsplit((parts.scheme, f"{addr}:{port}", parts.path or "/", parts.query, ""))
        with requests.Session() as session:
            # 只取代理配置，不加载用户的 .netrc 凭据。没有显式代理时由 TUN 转发固定 IP。
            session.trust_env = False
            proxies = requests.utils.get_environ_proxies(url) if ip in CLASH_FAKE_IP_RANGE else {}
            if requests.utils.select_proxy(url, proxies):
                target = url
            elif parts.scheme == "https":
                session.mount("https://", _PinnedHTTPSAdapter(host.encode("idna").decode()))
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ValueError("读取网页超时。")
            headers = {"Host": parts.netloc.encode("idna").decode(), "User-Agent": "D_sakiko/4 (web reader)",
                       "Accept": "text/markdown, text/html;q=0.9, text/plain;q=0.8", "Accept-Encoding": "identity"}
            with session.get(target, headers=headers, proxies=proxies, timeout=min(remaining, 10), allow_redirects=False, stream=True) as response:
                if response.is_redirect:
                    url = urljoin(url, response.headers["Location"])
                    continue
                if not 200 <= response.status_code < 300:
                    raise ValueError(f"网页返回 HTTP {response.status_code}。")
                mime = response.headers.get("Content-Type", "").partition(";")[0].strip().lower()
                if mime not in {"text/html", "application/xhtml+xml", "text/markdown", "text/plain"}:
                    raise ValueError("仅支持 HTML、Markdown 和纯文本页面。")
                data = bytearray()
                for chunk in response.iter_content(32 * 1024):
                    if time.monotonic() > deadline or len(data) + len(chunk) > MAX_BYTES:
                        raise ValueError("网页读取超时或超过 3 MiB。")
                    data.extend(chunk)
                if mime in {"text/html", "application/xhtml+xml"}:
                    return _html_page(url, bytes(data))
                encoding = response.encoding if "charset=" in response.headers.get("Content-Type", "").lower() else "utf-8-sig"
                return Page(url, host, data.decode(encoding or "utf-8", errors="replace").strip())
    raise ValueError("网页重定向次数过多。")


def _html_page(url: str, data: bytes) -> Page:
    """优先保留网页主区域及其标题、列表、链接和表格。"""
    from bs4 import BeautifulSoup
    from markdownify import MarkdownConverter

    soup = BeautifulSoup(data, "html.parser")
    title = soup.title.get_text(" ", strip=True) if soup.title else ""
    if soup.select_one('input[type="password"]'):
        raise ValueError("网页需要登录，无法读取公开正文。")
    root = soup.select_one("main, [role=main]") or soup.article or soup.body or soup
    for node in root.select("script, style, nav, footer, head, template, noscript"):
        node.decompose()
    for anchor in root.select("a[href]"):
        anchor["href"] = urljoin(url, str(anchor["href"]))
    content = MarkdownConverter(heading_style="ATX").convert_soup(root).strip()
    if not content:
        raise ValueError("没有可读正文，网页可能需要 JavaScript。")
    return Page(url, title or urlsplit(url).hostname or url, content)


class WebReader:
    """持有一轮工具调用专用的网页缓存。"""

    def __init__(self) -> None:
        """建立本轮空缓存。"""
        self.pages: dict[str, Page] = {}

    def read(self, arguments: dict[str, object]) -> dict[str, object]:
        """按固定字符预算返回一段网页及下次读取位置。"""
        url = str(arguments.get("url") or "").strip()
        offset = arguments.get("offset", 0)
        if type(offset) is not int or offset < 0:
            raise ValueError("offset 必须是非负整数。")
        page = self.pages.get(url)
        if page is None:
            page = _fetch(url)
            if not page.content:
                raise ValueError("网页没有可读的正文。")
            self.pages[url] = self.pages[page.url] = page
        if offset >= len(page.content):
            raise ValueError("offset 已超出网页正文。")
        end = offset + PAGE_SIZE
        return {"url": page.url, "title": page.title, "content": page.content[offset:end],
                "next_offset": end if end < len(page.content) else None}

    @staticmethod
    def display(result: object) -> str:
        """向用户展示来源，避免在工具详情里堆叠正文。"""
        if not isinstance(result, dict):
            return ""
        return f"已读取 {result['title']}\n{result['url']}"
