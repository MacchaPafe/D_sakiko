"""GPT-SoVITS 的显式资源契约与加载入口，不识别来源目录或下载方式。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Dict, Mapping, Optional, Tuple, cast

if TYPE_CHECKING:
    from transformers import BertForMaskedLM, BertTokenizerFast, HubertModel, Wav2Vec2FeatureExtractor


def object_value(value: object, role: str) -> Dict[str, object]:
    """验证配置对象，错误中保留用途以便定位缺失资源。"""
    if not isinstance(value, dict) or not all(isinstance(key, str) for key in value):
        raise ValueError(f"GPT-SoVITS {role} 必须是对象")
    return cast(Dict[str, object], value)


def file_path(value: object, role: str) -> str:
    """只接受宿主显式提供的可读绝对文件路径，不推断相邻文件。"""
    if not isinstance(value, str) or not Path(value).is_absolute():
        raise ValueError(f"GPT-SoVITS {role} 缺少绝对文件路径")
    path = Path(value)
    if not path.is_file():
        raise ValueError(f"GPT-SoVITS {role} 文件不存在或不是普通文件：{path}")
    try:
        with path.open("rb") as stream:
            stream.read(1)
    except OSError as error:
        raise ValueError(f"GPT-SoVITS {role} 文件不可读取：{path}") from error
    return str(path.resolve())


def group_files(value: object, role: str, names: Tuple[str, ...]) -> Dict[str, str]:
    """校验一组配套文件，拒绝遗漏和拼写错误。"""
    values = object_value(value, role)
    unknown = set(values) - set(names)
    if unknown:
        raise ValueError(f"GPT-SoVITS {role} 未知用途：{sorted(unknown)}")
    return {name: file_path(values.get(name), f"{role}.{name}") for name in names}


@dataclass(frozen=True)
class BertFiles:
    """BERT 配置、单文件 PyTorch 权重及 tokenizer.json，各自可独立存放。"""

    config: str
    weights: str
    tokenizer: str


@dataclass(frozen=True)
class HubertFiles:
    """HuBERT 配置、单文件 PyTorch 权重及特征提取配置。"""

    config: str
    weights: str
    feature_extractor: str


@dataclass(frozen=True)
class G2pwFiles:
    """G2PW 的 ONNX、配置和发音映射文件，不包含训练记录。"""

    model: str
    config: str
    polyphonic_characters: str
    monophonic_characters: str
    bopomofo_to_pinyin: str
    character_bopomofo: str


@dataclass(frozen=True)
class GptSovitsResources:
    """一个进程固定的资源快照，角色权重仍随任务提供。"""

    bert: BertFiles
    hubert: HubertFiles
    g2pw: G2pwFiles
    language_detector: str
    base_sovits: Optional[str] = None
    vocoder: Optional[str] = None
    speaker_encoder: Optional[str] = None

    @classmethod
    def parse(cls, value: object, architecture: str) -> GptSovitsResources:
        """在加载推理依赖之前检查当前架构的全部启动文件。"""
        if architecture not in ("v1", "v2", "v4", "v2Pro", "v2ProPlus"):
            raise ValueError(f"不支持的 GPT-SoVITS 架构：{architecture}")
        values = object_value(value, "resources")
        allowed = {"bert", "hubert", "g2pw", "languageDetector", "baseSovits", "vocoder", "speakerEncoder"}
        if set(values) - allowed:
            raise ValueError(f"GPT-SoVITS 未知资源用途：{sorted(set(values) - allowed)}")
        bert = group_files(values.get("bert"), "bert", ("config", "weights", "tokenizer"))
        hubert = group_files(values.get("hubert"), "hubert", ("config", "weights", "featureExtractor"))
        g2pw = group_files(values.get("g2pw"), "g2pw", (
            "model", "config", "polyphonicCharacters", "monophonicCharacters",
            "bopomofoToPinyin", "characterBopomofo",
        ))
        detector = group_files(values.get("languageDetector"), "languageDetector", ("model",))
        optional: Dict[str, Optional[str]] = {}
        for role in ("baseSovits", "vocoder", "speakerEncoder"):
            required = (role == "vocoder" and architecture == "v4") or (
                role == "speakerEncoder" and architecture in ("v2Pro", "v2ProPlus")
            )
            optional[role] = file_path(values.get(role), role) if required or role in values else None
        return cls(
            BertFiles(**bert),
            HubertFiles(hubert["config"], hubert["weights"], hubert["featureExtractor"]),
            G2pwFiles(g2pw["model"], g2pw["config"], g2pw["polyphonicCharacters"],
                      g2pw["monophonicCharacters"], g2pw["bopomofoToPinyin"], g2pw["characterBopomofo"]),
            detector["model"], optional["baseSovits"], optional["vocoder"], optional["speakerEncoder"],
        )

    def require(self, name: str) -> str:
        """按实际角色需求检查可选资源，例如 LoRA 需要基础 SoVITS 权重。"""
        value = getattr(self, name, None)
        if not isinstance(value, str):
            raise ValueError(f"缺少 GPT-SoVITS 资源：{name}")
        return value


_text_resources: Optional[GptSovitsResources] = None


def configure_text_resources(resources: GptSovitsResources) -> None:
    """为旧文本模块的导入期初始化提供一次性快照；每个 worker 只加载一个配置。"""
    global _text_resources
    if _text_resources is not None and _text_resources != resources:
        raise RuntimeError("不能在运行中的 worker 内替换 GPT-SoVITS 文本资源")
    _text_resources = resources


def text_resources() -> GptSovitsResources:
    """供文本预处理模块取得已验证的文件，不从环境或工作目录补全。"""
    if _text_resources is None:
        raise RuntimeError("尚未配置 GPT-SoVITS 文本资源")
    return _text_resources


def load_bert_tokenizer(files: BertFiles) -> BertTokenizerFast:
    """从单独的 tokenizer.json 加载，特殊符号沿用当前中文 BERT 的固定契约。"""
    from transformers import BertTokenizerFast
    return BertTokenizerFast(tokenizer_file=files.tokenizer)


def load_bert(files: BertFiles) -> Tuple[BertTokenizerFast, BertForMaskedLM]:
    """显式提供配置和权重，保留 Transformers 的权重键转换与权重绑定处理。"""
    import torch
    from transformers import BertConfig, BertForMaskedLM
    config = BertConfig.from_json_file(files.config)
    if config.model_type != "bert":
        raise ValueError("bert.config 必须描述 BERT 模型")
    state = torch.load(files.weights, map_location="cpu", weights_only=True)
    model, info = BertForMaskedLM.from_pretrained(
        None, config=config, state_dict=state, local_files_only=True, output_loading_info=True,
    )
    _check_loading(info, "bert.weights")
    return load_bert_tokenizer(files), model


def load_hubert(files: HubertFiles) -> Tuple[HubertModel, Wav2Vec2FeatureExtractor]:
    """分别加载 HuBERT 模型和特征提取配置，不要求它们拥有共同父目录。"""
    import torch
    from transformers import HubertConfig, HubertModel, Wav2Vec2FeatureExtractor
    config = HubertConfig.from_json_file(files.config)
    if config.model_type != "hubert":
        raise ValueError("hubert.config 必须描述 HuBERT 模型")
    state = torch.load(files.weights, map_location="cpu", weights_only=True)
    model, info = HubertModel.from_pretrained(
        None, config=config, state_dict=state, local_files_only=True, output_loading_info=True,
    )
    _check_loading(info, "hubert.weights")
    extractor = Wav2Vec2FeatureExtractor.from_json_file(files.feature_extractor)
    return model, extractor


def _check_loading(info: Mapping[str, object], role: str) -> None:
    """拒绝缺失权重导致的随机初始化，允许库已识别并忽略的兼容键。"""
    if info.get("missing_keys") or info.get("mismatched_keys") or info.get("error_msgs"):
        raise ValueError(f"GPT-SoVITS {role} 与模型配置不匹配：{dict(info)}")
