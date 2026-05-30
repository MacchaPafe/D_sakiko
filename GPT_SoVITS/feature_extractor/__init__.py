# 原始代码来自：https://github.com/RVC-Boss/GPT-SoVITS/blob/main/GPT_SoVITS/feature_extractor/__init__.py
from . import cnhubert, whisper_enc

content_module_map = {"cnhubert": cnhubert, "whisper": whisper_enc}
