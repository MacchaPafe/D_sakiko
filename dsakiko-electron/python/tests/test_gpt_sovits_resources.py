"""验证显式资源契约及真实小模型加载，不依赖下载或固定目录结构。"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Dict, cast
from unittest.mock import patch

PYTHON_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PYTHON_ROOT))
sys.path.insert(0, str(PYTHON_ROOT / "vendor" / "gpt_sovits"))

from speech.gpt_sovits_resources import (
    GptSovitsResources, load_bert, load_hubert,
)


def manifest_at(root: Path) -> Dict[str, object]:
    """把每个用途放在独立目录和无后缀文件中，避免测试意外依赖原始布局。"""
    groups = {
        "bert": ("config", "weights", "tokenizer"),
        "hubert": ("config", "weights", "featureExtractor"),
        "g2pw": ("model", "config", "polyphonicCharacters", "monophonicCharacters",
                 "bopomofoToPinyin", "characterBopomofo"),
        "languageDetector": ("model",),
    }
    result: Dict[str, object] = {}
    count = 0
    for group, roles in groups.items():
        files: Dict[str, str] = {}
        for role in roles:
            count += 1
            file = root / f"分散 {count}" / "无后缀"
            file.parent.mkdir(parents=True)
            file.write_text("placeholder", encoding="utf-8")
            files[role] = str(file)
        result[group] = files
    return result


class ResourceManifestTests(unittest.TestCase):
    """无需推理依赖即可在启动前报告明确的资源问题。"""

    def test_scattered_files_and_optional_architecture_resources(self) -> None:
        """清单不要求共同目录，额外架构文件按需求检查。"""
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest = manifest_at(root)
            resources = GptSovitsResources.parse(manifest, "v2")
            self.assertNotEqual(Path(resources.bert.config).parent, Path(resources.bert.weights).parent)
            with self.assertRaisesRegex(ValueError, "vocoder"):
                GptSovitsResources.parse(manifest, "v4")
            with self.assertRaisesRegex(ValueError, "speakerEncoder"):
                GptSovitsResources.parse(manifest, "v2ProPlus")
            manifest["vocoder"] = resources.bert.weights
            self.assertEqual(GptSovitsResources.parse(manifest, "v4").vocoder, resources.bert.weights)
            with self.assertRaisesRegex(ValueError, "base_sovits"):
                resources.require("base_sovits")

    def test_missing_unknown_relative_and_directory_paths(self) -> None:
        """错误必须指明用途，不能回退到当前目录或自动找相邻文件。"""
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest = manifest_at(root)
            bert = cast(Dict[str, str], manifest["bert"])
            for invalid in ("relative", str(root), str(root / "missing")):
                bert["config"] = invalid
                with self.assertRaisesRegex(ValueError, "bert.config"):
                    GptSovitsResources.parse(manifest, "v2")
            manifest["bert"] = {"unexpected": str(root)}
            with self.assertRaisesRegex(ValueError, "未知用途"):
                GptSovitsResources.parse(manifest, "v2")
            with self.assertRaisesRegex(ValueError, "不支持"):
                GptSovitsResources.parse(manifest, "v3")


HAS_MODELS = all(importlib.util.find_spec(name) is not None for name in ("torch", "transformers", "tokenizers"))


@unittest.skipUnless(HAS_MODELS, "需要项目 Python 推理环境")
class ExplicitModelLoadingTests(unittest.TestCase):
    """通过真实小型 Transformers 网络验证文件装配和输出一致性。"""

    def setUp(self) -> None:
        """限制线程并关闭联网，测试不会下载模型。"""
        import torch
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.old_threads = torch.get_num_threads()
        torch.set_num_threads(1)
        self.addCleanup(torch.set_num_threads, self.old_threads)
        self.environment = patch.dict(os.environ, {"HF_HUB_OFFLINE": "1", "TOKENIZERS_PARALLELISM": "false"})
        self.environment.start()
        self.addCleanup(self.environment.stop)
        self.manifest = manifest_at(self.root)
        self.resources = GptSovitsResources.parse(self.manifest, "v2")

    def prepare_bert(self) -> object:
        """生成小型权重和真实分词器，文件名均不带格式后缀。"""
        import torch
        from tokenizers import BertWordPieceTokenizer
        from transformers import BertConfig, BertForMaskedLM
        vocab = self.root / "temporary-vocabulary"
        vocab.write_text("[PAD]\n[UNK]\n[CLS]\n[SEP]\n[MASK]\n你\n好\n", encoding="utf-8")
        tokenizer = BertWordPieceTokenizer(str(vocab), lowercase=True)
        tokenizer.save(self.resources.bert.tokenizer)
        vocab.unlink()
        config = BertConfig(vocab_size=7, hidden_size=8, num_hidden_layers=1,
                            num_attention_heads=2, intermediate_size=16)
        config.to_json_file(self.resources.bert.config)
        model = BertForMaskedLM(config).eval()
        torch.save(model.state_dict(), self.resources.bert.weights)
        return model

    def test_bert_outputs_match_and_missing_weights_fail(self) -> None:
        """显式装配保持分词与模型输出，缺失权重不得随机初始化。"""
        import torch
        from transformers import BertForMaskedLM
        original = cast(BertForMaskedLM, self.prepare_bert())
        tokenizer, loaded = load_bert(self.resources.bert)
        inputs = tokenizer("你好", return_tensors="pt")
        self.assertEqual(inputs["input_ids"].tolist(), [[2, 5, 6, 3]])
        with torch.no_grad():
            torch.testing.assert_close(original(**inputs).logits, loaded(**inputs).logits)
        state = original.state_dict()
        del state["bert.embeddings.word_embeddings.weight"]
        del state["cls.predictions.decoder.weight"]
        torch.save(state, self.resources.bert.weights)
        with self.assertRaisesRegex(ValueError, "bert.weights"):
            load_bert(self.resources.bert)

    def test_hubert_outputs_match(self) -> None:
        """独立配置、权重和特征提取文件恢复同一模型，参数不依赖文件名。"""
        import torch
        from transformers import HubertConfig, HubertModel, Wav2Vec2FeatureExtractor
        config = HubertConfig(
            hidden_size=8, num_hidden_layers=1, num_attention_heads=2, intermediate_size=16,
            conv_dim=(4, 4, 4), conv_stride=(2, 2, 2), conv_kernel=(3, 3, 3),
            num_conv_pos_embeddings=4, num_conv_pos_embedding_groups=2,
            mask_time_prob=0.0, feat_extract_norm="layer",
        )
        config.to_json_file(self.resources.hubert.config)
        original = HubertModel(config).eval()
        torch.save(original.state_dict(), self.resources.hubert.weights)
        extractor = Wav2Vec2FeatureExtractor(sampling_rate=16000, do_normalize=True)
        extractor.to_json_file(self.resources.hubert.feature_extractor)
        loaded, actual_extractor = load_hubert(self.resources.hubert)
        self.assertEqual(actual_extractor.to_dict(), extractor.to_dict())
        inputs = torch.randn(1, 128)
        with torch.no_grad():
            torch.testing.assert_close(original(inputs).last_hidden_state, loaded(inputs).last_hidden_state)

    @unittest.skipUnless(importlib.util.find_spec("onnxruntime") is not None, "需要 ONNX Runtime")
    def test_g2pw_uses_all_explicit_files(self) -> None:
        """G2PW 配置无 .py 后缀仍可加载，所有字表和 ONNX 使用清单路径。"""
        self.prepare_bert()
        from text.g2pw.onnx_api import G2PWOnnxConverter
        files = self.resources.g2pw
        Path(files.config).write_text("use_char_phoneme = False\n", encoding="utf-8")
        Path(files.polyphonic_characters).write_text("你\tㄋㄧ3\n", encoding="utf-8")
        Path(files.monophonic_characters).write_text("好\tㄏㄠ3\n", encoding="utf-8")
        Path(files.bopomofo_to_pinyin).write_text('{"ㄋㄧ": "ni", "ㄏㄠ": "hao"}', encoding="utf-8")
        Path(files.character_bopomofo).write_text('{"你": "ㄋㄧ3"}', encoding="utf-8")
        with patch("onnxruntime.InferenceSession") as session:
            converter = G2PWOnnxConverter(files, self.resources.bert, style="pinyin")
        self.assertEqual(session.call_args.args[0], files.model)
        self.assertEqual(converter._convert_bopomofo_to_pinyin("ㄋㄧ3"), "ni3")
        self.assertEqual(converter.monophonic_chars_dict["好"], "ㄏㄠ3")
        self.assertEqual(converter.char_bopomofo_dict, {"你": "ㄋㄧ3"})
        self.assertEqual(list(self.root.rglob("__pycache__")), [])


if __name__ == "__main__":
    unittest.main()
