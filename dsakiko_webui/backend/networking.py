from __future__ import annotations

import ipaddress
import socket
from dataclasses import dataclass

import psutil


HOME_LAN_NETWORK = ipaddress.IPv4Network("192.168.0.0/16")
PRIVATE_LAN_NETWORKS = (
    ipaddress.IPv4Network("10.0.0.0/8"),
    ipaddress.IPv4Network("172.16.0.0/12"),
)
TUNNEL_INTERFACE_PREFIXES = ("utun", "tun", "tap", "wg")


@dataclass(frozen=True)
class NetworkAddress:
    """描述一个可用于手机访问的私有 IPv4 地址。"""

    address: str
    interface_name: str
    is_default: bool


def default_route_ipv4() -> str | None:
    """通过无数据 UDP 探测取得当前默认 IPv4 路由的本机地址。"""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.connect(("192.0.2.1", 80))
            return probe.getsockname()[0]
    except OSError:
        return None


def _is_tunnel_interface(interface_name: str) -> bool:
    """判断网卡名称是否属于常见隧道接口。"""
    normalized = interface_name.strip().lower()
    return normalized.startswith(TUNNEL_INTERFACE_PREFIXES)


def _address_priority(item: NetworkAddress) -> int:
    """按照家庭私网、其他 RFC 1918 私网和隧道网卡划分优先级。"""
    if _is_tunnel_interface(item.interface_name):
        return 3
    address = ipaddress.IPv4Address(item.address)
    if address in HOME_LAN_NETWORK:
        return 0
    if any(address in network for network in PRIVATE_LAN_NETWORKS):
        return 1
    return 2


def select_network_addresses(
    candidates: list[tuple[str, str]],
    default_address: str | None,
) -> list[NetworkAddress]:
    """过滤、去重，并按局域网类型及原有默认路由规则排序。"""
    selected: dict[str, NetworkAddress] = {}
    for interface_name, raw_address in candidates:
        try:
            address = ipaddress.ip_address(raw_address)
        except ValueError:
            continue
        if (
            not isinstance(address, ipaddress.IPv4Address)
            or not address.is_private
            or address.is_loopback
            or address.is_link_local
            or address.is_unspecified
        ):
            continue
        normalized = str(address)
        if normalized not in selected:
            selected[normalized] = NetworkAddress(
                address=normalized,
                interface_name=interface_name,
                is_default=normalized == default_address,
            )
    return sorted(
        selected.values(),
        key=lambda item: (
            _address_priority(item),
            not item.is_default,
            item.interface_name.lower(),
            item.address,
        ),
    )


def discover_network_addresses() -> list[NetworkAddress]:
    """枚举启用网卡上的私有 IPv4，并按局域网适用性返回。"""
    stats = psutil.net_if_stats()
    candidates: list[tuple[str, str]] = []
    for interface_name, addresses in psutil.net_if_addrs().items():
        interface_stats = stats.get(interface_name)
        if interface_stats is not None and not interface_stats.isup:
            continue
        for address in addresses:
            if address.family == socket.AF_INET:
                candidates.append((interface_name, address.address))
    return select_network_addresses(candidates, default_route_ipv4())
