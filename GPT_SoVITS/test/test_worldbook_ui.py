"""世界书只读 Qt 页面测试。"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from PyQt5.QtWidgets import QApplication
from qfluentwidgets import BodyLabel, ListWidget, PrimaryPushButton, PushButton, TextEdit

from ui.interfaces.worldbook_area import WorldbookArea


class WorldbookUiTest(unittest.TestCase):
    """验证查看器在模型和 Qdrant 不可用时仍可创建。"""

    @classmethod
    def setUpClass(cls) -> None:
        """创建测试进程共享的 QApplication。"""

        cls.app = QApplication.instance() or QApplication([])

    def test_area_loads_without_index_or_embedding_model(self) -> None:
        """只读 JSON 管理页面不得在构造时加载重型资源。"""

        with tempfile.TemporaryDirectory() as directory:
            area = WorldbookArea(app_root=Path(directory))
            area.show()
            self.app.processEvents()

            self.assertEqual(area.package_list.count(), 0)
            self.assertFalse(area._controller.is_rag_available())
            self.assertIsInstance(area.package_list, ListWidget)
            self.assertIsInstance(area.entry_list, ListWidget)
            self.assertIsInstance(area.detail_view, TextEdit)
            self.assertIsInstance(area.status_label, BodyLabel)
            self.assertIsInstance(area.sync_button, PrimaryPushButton)
            self.assertIsInstance(area.hide_button, PushButton)
            area.close()


if __name__ == "__main__":
    unittest.main()
