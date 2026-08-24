from __future__ import annotations

import unittest

from fastapi.testclient import TestClient

from dsakiko_webui.backend.auth import AccessController, PairingRejected
from dsakiko_webui.backend.networking import NetworkAddress
from dsakiko_webui.backend.pairing_ui import (
    PAIRING_UI_HEADER,
    PairingPresentation,
    PairingUiLocation,
    create_pairing_ui_app,
)


class PairingUiTest(unittest.TestCase):
    """验证本机配对展示接口的授权和状态行为。"""

    def setUp(self) -> None:
        """创建固定地址、端口和 nonce 的本机展示应用。"""
        self.access = AccessController("123456")
        self.presentation = PairingPresentation(
            self.access,
            webui_port=8000,
            address_discovery=lambda: [NetworkAddress("192.168.1.20", "en0", True)],
        )
        self.location = PairingUiLocation(8765, "ui-secret")
        self.app = create_pairing_ui_app(self.presentation, self.location)
        self.headers = {PAIRING_UI_HEADER: self.location.nonce}
        self.origin_headers = {
            **self.headers,
            "Origin": "http://127.0.0.1:8765",
        }

    def test_static_shell_contains_no_access_code(self) -> None:
        """无 nonce 的静态页面不能包含备用访问码。"""
        with TestClient(self.app, base_url="http://127.0.0.1:8765") as client:
            response = client.get("/")
            self.assertEqual(response.status_code, 200)
            self.assertNotIn("123456", response.text)
            self.assertEqual(response.headers["Referrer-Policy"], "no-referrer")

    def test_sensitive_state_requires_ui_nonce(self) -> None:
        """状态和展示数据必须携带当前启动周期的 UI nonce。"""
        with TestClient(self.app, base_url="http://127.0.0.1:8765") as client:
            self.assertEqual(client.get("/api/state").status_code, 403)
            self.assertEqual(client.get("/api/state", headers=self.headers).status_code, 200)

    def test_shared_neutral_mascot_is_served_as_static_asset(self) -> None:
        """本机配对页应能加载与访问码页面共用的中性角色图。"""
        with TestClient(self.app, base_url="http://127.0.0.1:8765") as client:
            response = client.get("/neutral.png")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.headers["Content-Type"], "image/png")
            self.assertGreater(len(response.content), 0)

    def test_state_does_not_contain_pairing_token(self) -> None:
        """轮询状态只返回版本和状态，不泄漏配对凭证。"""
        with TestClient(self.app, base_url="http://127.0.0.1:8765") as client:
            state = client.get("/api/state", headers=self.headers)
            presentation = client.get("/api/presentation", headers=self.headers)
            pairing_url = presentation.json()["pairing_url"]
            pairing_token = pairing_url.split("#pair=", 1)[1]
            self.assertNotIn(pairing_token, state.text)
            self.assertIn("<svg", presentation.json()["qr_svg"])
            self.assertIn("viewBox=", presentation.json()["qr_svg"])

    def test_presentation_includes_plain_fallback_url(self) -> None:
        """展示数据应明确提供不含配对凭证的普通访问地址。"""
        with TestClient(self.app, base_url="http://127.0.0.1:8765") as client:
            presentation = client.get(
                "/api/presentation",
                headers=self.headers,
            ).json()
            self.assertEqual(
                presentation["fallback_url"],
                "http://192.168.1.20:8000/",
            )
            self.assertNotIn("#pair=", presentation["fallback_url"])

    def test_mutation_requires_same_origin(self) -> None:
        """重新生成等修改请求必须来自本机页面同源 Origin。"""
        with TestClient(self.app, base_url="http://127.0.0.1:8765") as client:
            denied = client.post("/api/regenerate", headers=self.headers)
            allowed = client.post("/api/regenerate", headers=self.origin_headers)
            self.assertEqual(denied.status_code, 403)
            self.assertEqual(allowed.status_code, 200)

    def test_regenerate_revokes_previous_pairing_token(self) -> None:
        """本机重新生成二维码后旧配对凭证应立即失效。"""
        with TestClient(self.app, base_url="http://127.0.0.1:8765") as client:
            first = client.get("/api/presentation", headers=self.headers).json()
            old_token = first["pairing_url"].split("#pair=", 1)[1]
            client.post("/api/regenerate", headers=self.origin_headers)
            with self.assertRaises(PairingRejected):
                self.access.login_with_pairing(old_token, "phone")

    def test_unknown_address_cannot_be_selected(self) -> None:
        """地址切换接口不能把任意 Host 写入配对 URL。"""
        with TestClient(self.app, base_url="http://127.0.0.1:8765") as client:
            response = client.post(
                "/api/address",
                headers=self.origin_headers,
                json={"address": "10.0.0.99"},
            )
            self.assertEqual(response.status_code, 400)


if __name__ == "__main__":
    unittest.main()
