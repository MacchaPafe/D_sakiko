from __future__ import annotations

import threading
import unittest
from concurrent.futures import ThreadPoolExecutor

from dsakiko_webui.backend.auth import (
    AccessCodeRejected,
    AccessController,
    AccessRateLimited,
    PairingRejected,
)


class FakeClock:
    """提供可由测试精确推进的单调时钟。"""

    def __init__(self) -> None:
        """从固定时刻开始计时。"""
        self.value = 1_000.0

    def __call__(self) -> float:
        """返回当前测试时刻。"""
        return self.value

    def advance(self, seconds: float) -> None:
        """将测试时钟向前推进指定秒数。"""
        self.value += seconds


class AccessControllerTest(unittest.TestCase):
    """验证统一访问控制模块的状态转换和安全约束。"""

    def setUp(self) -> None:
        """为每个用例创建独立时钟和控制器。"""
        self.clock = FakeClock()
        self.controller = AccessController("123456", clock=self.clock)

    def test_access_code_replaces_the_active_session(self) -> None:
        """正确访问码应签发新会话并替换旧控制端。"""
        first = self.controller.login_with_access_code("123456", "192.168.1.2", "one")
        second = self.controller.login_with_access_code("123456", "192.168.1.3", "two")
        self.assertFalse(first.replaced_existing_controller)
        self.assertTrue(second.replaced_existing_controller)
        self.assertEqual(second.replaced_token, first.token)
        self.assertFalse(self.controller.is_authenticated(first.token))
        self.assertTrue(self.controller.is_authenticated(second.token))

    def test_token_bucket_limits_the_sixth_immediate_failure(self) -> None:
        """单个来源连续五次失败后应等待令牌恢复。"""
        for _ in range(5):
            with self.assertRaises(AccessCodeRejected):
                self.controller.login_with_access_code("000000", "192.168.1.2")
        with self.assertRaises(AccessRateLimited) as raised:
            self.controller.login_with_access_code("000000", "192.168.1.2")
        self.assertEqual(raised.exception.retry_after_seconds, 12)

    def test_twentieth_failure_starts_one_minute_cooldown(self) -> None:
        """十五分钟内第二十次失败应首次触发一分钟冷却。"""
        for _ in range(20):
            with self.assertRaises(AccessCodeRejected):
                self.controller.login_with_access_code("000000", "192.168.1.2")
            self.clock.advance(12)
        with self.assertRaises(AccessRateLimited) as raised:
            self.controller.login_with_access_code("000000", "192.168.1.2")
        self.assertGreaterEqual(raised.exception.retry_after_seconds, 48)
        self.assertLessEqual(raised.exception.retry_after_seconds, 60)

    def test_success_clears_source_failure_history(self) -> None:
        """成功认证应清除该来源的失败和冷却阶段。"""
        for _ in range(5):
            with self.assertRaises(AccessCodeRejected):
                self.controller.login_with_access_code("000000", "192.168.1.2")
            self.clock.advance(12)
        self.controller.login_with_access_code("123456", "192.168.1.2")
        for _ in range(5):
            with self.assertRaises(AccessCodeRejected):
                self.controller.login_with_access_code("000000", "192.168.1.2")
            self.clock.advance(12)

    def test_pairing_is_single_use_and_idempotent_for_same_client(self) -> None:
        """同一客户端可短时重试，但其他客户端不能复用凭证。"""
        grant = self.controller.regenerate_pairing()
        first = self.controller.login_with_pairing(grant.token, "phone-one")
        retry = self.controller.login_with_pairing(grant.token, "phone-one")
        self.assertTrue(retry.idempotent)
        self.assertEqual(retry.token, first.token)
        with self.assertRaises(PairingRejected):
            self.controller.login_with_pairing(grant.token, "phone-two")

    def test_pairing_expires_after_five_minutes(self) -> None:
        """配对凭证到达五分钟边界时应失效。"""
        grant = self.controller.regenerate_pairing()
        self.clock.advance(300)
        self.assertEqual(self.controller.pairing_snapshot().status, "expired")
        with self.assertRaises(PairingRejected):
            self.controller.login_with_pairing(grant.token, "phone")

    def test_replaced_session_cannot_be_restored_by_pairing_retry(self) -> None:
        """其他登录接管后不得借幂等窗口恢复旧会话。"""
        grant = self.controller.regenerate_pairing()
        self.controller.login_with_pairing(grant.token, "phone-one")
        self.controller.login_with_access_code("123456", "192.168.1.3", "phone-two")
        with self.assertRaises(PairingRejected):
            self.controller.login_with_pairing(grant.token, "phone-one")

    def test_only_one_concurrent_client_consumes_pairing(self) -> None:
        """两个并发客户端中只能有一个完成首次兑换。"""
        grant = self.controller.regenerate_pairing()
        barrier = threading.Barrier(2)

        def redeem(client_id: str) -> bool:
            """等待并发起跑后尝试为指定客户端兑换凭证。"""
            barrier.wait()
            try:
                self.controller.login_with_pairing(grant.token, client_id)
                return True
            except PairingRejected:
                return False

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(redeem, ("one", "two")))
        self.assertEqual(results.count(True), 1)

    def test_pairing_failures_do_not_consume_access_code_budget(self) -> None:
        """无效配对请求不应影响六位码令牌桶。"""
        for _ in range(20):
            with self.assertRaises(PairingRejected):
                self.controller.login_with_pairing("x" * 43, "phone")
        self.controller.login_with_access_code("123456", "192.168.1.2")

    def test_global_defense_uses_stricter_bucket_and_later_exits(self) -> None:
        """全局失败达到门槛后应收紧额度，并在滞回条件满足后退出。"""
        for index in range(100):
            with self.assertRaises(AccessCodeRejected):
                self.controller.login_with_access_code("000000", f"10.0.0.{index + 1}")
        with self.assertRaises(AccessCodeRejected):
            self.controller.login_with_access_code("000000", "192.168.1.2")
        with self.assertRaises(AccessCodeRejected):
            self.controller.login_with_access_code("000000", "192.168.1.2")
        with self.assertRaises(AccessRateLimited) as raised:
            self.controller.login_with_access_code("000000", "192.168.1.2")
        self.assertEqual(raised.exception.retry_after_seconds, 30)

        self.clock.advance(601)
        for _ in range(5):
            with self.assertRaises(AccessCodeRejected):
                self.controller.login_with_access_code("000000", "192.168.1.3")
        with self.assertRaises(AccessRateLimited) as normal_limit:
            self.controller.login_with_access_code("000000", "192.168.1.3")
        self.assertEqual(normal_limit.exception.retry_after_seconds, 12)


if __name__ == "__main__":
    unittest.main()
