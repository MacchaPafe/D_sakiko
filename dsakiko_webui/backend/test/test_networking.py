from __future__ import annotations

import unittest

from dsakiko_webui.backend.networking import select_network_addresses


class NetworkAddressTest(unittest.TestCase):
    """验证局域网 IPv4 地址的筛选和排序。"""

    def test_home_lan_precedes_default_tunnel_address(self) -> None:
        """192.168 非隧道地址应排在默认路由对应的隧道地址之前。"""
        result = select_network_addresses([
            ("utun3", "10.0.0.8"),
            ("en0", "192.168.1.20"),
        ], "10.0.0.8")
        self.assertEqual([item.address for item in result], ["192.168.1.20", "10.0.0.8"])

    def test_home_lan_precedes_other_non_tunnel_private_networks(self) -> None:
        """192.168 非隧道地址应优先于 10 和 172.16 至 172.31 地址。"""
        result = select_network_addresses([
            ("en2", "172.20.1.4"),
            ("en1", "10.0.0.8"),
            ("en0", "192.168.1.20"),
        ], "10.0.0.8")
        self.assertEqual(
            [item.address for item in result],
            ["192.168.1.20", "10.0.0.8", "172.20.1.4"],
        )

    def test_default_route_remains_first_within_same_priority(self) -> None:
        """同一地址优先级内仍应将默认路由对应地址排在前面。"""
        result = select_network_addresses([
            ("en0", "10.0.0.8"),
            ("en1", "172.20.1.4"),
        ], "172.20.1.4")
        self.assertEqual(
            [item.address for item in result],
            ["172.20.1.4", "10.0.0.8"],
        )

    def test_all_supported_tunnel_prefixes_are_ranked_last(self) -> None:
        """常见隧道接口应统一排在所有非隧道候选地址之后。"""
        result = select_network_addresses([
            ("utun0", "192.168.10.2"),
            ("tun1", "10.1.0.2"),
            ("tap2", "172.20.0.2"),
            ("wg0", "192.168.30.2"),
            ("en0", "198.18.0.1"),
        ], "192.168.10.2")
        self.assertEqual(result[0].interface_name, "en0")
        self.assertEqual(
            [item.interface_name for item in result[1:]],
            ["utun0", "tap2", "tun1", "wg0"],
        )

    def test_invalid_loopback_link_local_and_public_addresses_are_filtered(self) -> None:
        """不可由手机作为局域网目标的地址不应进入候选列表。"""
        result = select_network_addresses([
            ("lo0", "127.0.0.1"),
            ("en0", "169.254.10.2"),
            ("en0", "8.8.8.8"),
            ("en0", "not-an-ip"),
            ("en0", "172.16.4.2"),
        ], None)
        self.assertEqual([item.address for item in result], ["172.16.4.2"])

    def test_duplicate_addresses_are_removed_stably(self) -> None:
        """同一地址出现在多个网卡时只保留首个稳定候选。"""
        result = select_network_addresses([
            ("en1", "192.168.1.20"),
            ("en0", "192.168.1.20"),
        ], None)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].interface_name, "en1")


if __name__ == "__main__":
    unittest.main()
