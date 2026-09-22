"""保护桌面启动的轻量导入边界和首次检索时的模型加载行为。"""

from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
from types import ModuleType
from unittest import TestCase
from unittest.mock import Mock, patch


class LazyRagImportTests(TestCase):
    """验证未查询世界书时不加载推理依赖，查询时仍可正常编码。"""

    def test_main_and_rag_services_do_not_import_inference_libraries(self) -> None:
        """在独立解释器中检查完整主入口，避免已导入模块掩盖回归。"""
        source = Path(__file__).resolve().parents[1]
        script = """
import sys
import main2
from rag.services import EmbeddingProvider
provider = EmbeddingProvider('unused-local-model')
assert not provider.is_loaded()
heavy = {'torch', 'transformers', 'sentence_transformers', 'litellm'}
assert not heavy.intersection(sys.modules), sorted(heavy.intersection(sys.modules))
"""
        environment = dict(os.environ, PYTHONPATH=str(source))
        result = subprocess.run(
            [sys.executable, "-c", script],
            cwd=source,
            env=environment,
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_first_encoding_loads_once_and_reuses_the_model(self) -> None:
        """首次编码才创建模型，维度查询和后续编码复用已加载模型。"""
        from rag.services import EmbeddingProvider

        model = Mock()
        model.get_embedding_dimension.return_value = 2
        model.encode.return_value = [[0.25, 0.75]]
        constructor = Mock(return_value=model)
        module = ModuleType("sentence_transformers")
        module.SentenceTransformer = constructor
        with patch.dict(sys.modules, {"sentence_transformers": module}):
            provider = EmbeddingProvider("local-model", default_batch_size=8)
            constructor.assert_not_called()
            self.assertEqual(provider.encode_text("世界书问题"), [0.25, 0.75])
            self.assertEqual(provider.get_dimension(), 2)
            self.assertEqual(provider.encode_text("另一个问题"), [0.25, 0.75])
            constructor.assert_called_once_with("local-model")
            model.encode.assert_called_with(
                ["另一个问题"], batch_size=8, show_progress_bar=False
            )
