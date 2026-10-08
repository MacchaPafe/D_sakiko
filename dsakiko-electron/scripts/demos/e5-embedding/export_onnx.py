"""从项目本地 E5 权重导出独立的 JS 模型目录。"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from pathlib import Path

import torch
from transformers import AutoModel, AutoTokenizer


class TokenEncoder(torch.nn.Module):
    """只导出词元特征，将池化和归一化留给 JS。"""

    def __init__(self, model: torch.nn.Module) -> None:
        """保存原始模型。"""
        super().__init__()
        self.model = model

    def forward(self, input_ids: torch.Tensor, attention_mask: torch.Tensor) -> torch.Tensor:
        """返回最后一层词元特征。"""
        return self.model(input_ids=input_ids, attention_mask=attention_mask).last_hidden_state


def sha256(path: Path) -> str:
    """分块计算权重摘要，避免额外占用整份权重的内存。"""
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> None:
    """将本地 FP32 权重导出为支持动态批次和长度的 ONNX。"""
    root = Path(__file__).resolve().parents[4]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=root / "GPT_SoVITS/pretrained_models/multilingual-e5-small")
    parser.add_argument("--output", type=Path, default=root / "GPT_SoVITS/pretrained_models/multilingual-e5-small-onnx")
    args = parser.parse_args()
    source: Path = args.source.resolve()
    output: Path = args.output.resolve()
    if output == source or source in output.parents:
        raise ValueError("输出必须位于原模型目录之外，避免改变 Python 索引指纹")
    destination = output / "onnx/model.onnx"
    if destination.exists():
        raise FileExistsError(f"输出已存在，请使用新的 --output 路径: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    torch.set_num_threads(4)
    model = AutoModel.from_pretrained(str(source), local_files_only=True, attn_implementation="eager").eval()
    tokenizer = AutoTokenizer.from_pretrained(str(source), local_files_only=True)
    dummy = tokenizer(["query: 祥子为什么离开乐队？", "passage: 乐队成员准备演出。"], padding=True, return_tensors="pt")
    torch.onnx.export(
        TokenEncoder(model).eval(),
        (dummy["input_ids"], dummy["attention_mask"]),
        str(destination),
        input_names=["input_ids", "attention_mask"],
        output_names=["last_hidden_state"],
        dynamic_axes={name: {0: "batch", 1: "sequence"} for name in ("input_ids", "attention_mask", "last_hidden_state")},
        opset_version=17,
        dynamo=False,
    )
    for name in ("config.json", "tokenizer.json", "tokenizer_config.json", "special_tokens_map.json"):
        shutil.copy2(source / name, output / name)
    metadata: dict[str, object] = {
        "source_weight_sha256": sha256(source / "model.safetensors"),
        "onnx_sha256": sha256(destination),
        "onnx_bytes": destination.stat().st_size,
        "precision": "fp32",
        "opset": 17,
        "dimension": 384,
        "max_tokens": 512,
        "torch_version": torch.__version__,
        "pooling": "attention-mask-mean",
        "normalization": "l2",
    }
    (output / "export_metadata.json").write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"output": str(output), **metadata}, indent=2))


if __name__ == "__main__":
    main()
