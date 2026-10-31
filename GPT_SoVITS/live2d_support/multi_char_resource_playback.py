"""小剧场资源刷新后的安全热加载：已开始的原生动作不会被释放。"""
import hashlib
import json
import tempfile
from pathlib import Path

from live2d_support.performance_catalog import cubism_motion_json


class RefreshedPlayback:
    """按内容版本追加原生资源，下一次触发才读取刷新后的文件。"""

    def __init__(self):
        """建立每个模型实例独立的资源版本索引。"""
        self.versions = {}

    def discard(self, model):
        """模型销毁前丢弃其索引，避免后续换装误用旧原生资源编号。"""
        self.versions = {key: value for key, value in self.versions.items() if key[0] != id(model)}

    def play(self, model, kind, path, entry=None):
        """加载或复用当前文件版本；原生旧版本保留到模型销毁。"""
        path = Path(path)
        content = path.read_bytes()
        digest = hashlib.sha256(content + str(path).encode("utf-8")).hexdigest()
        key = (id(model), kind, digest)
        native = model.model
        if kind == "expressions":
            expression = self.versions.get(key)
            if expression is None:
                expression = "__theater_refresh_" + digest
                native.LoadExtraExpression(expression, str(path))
                self.versions[key] = expression
            native.SetExpression(expression)
            model.performance_expression_active = True
            model._default_expression_active = False
            return True
        index = self.versions.get(key)
        group = "__theater_refreshed_motions__"
        if index is None:
            if model.version == "v3":
                data = json.loads(content)
                data.setdefault("Meta", {})["Loop"] = False
                for name in ("FadeInTime", "FadeOutTime"):
                    if name in (entry or {}):
                        data[name] = entry[name]
                with tempfile.TemporaryDirectory(prefix="multi-char-motion-") as folder:
                    temporary = Path(folder) / path.name
                    temporary.write_text(cubism_motion_json(data), encoding="utf-8")
                    index = int(native.LoadExtraMotion(group, str(temporary)))
            else:
                index = int(native.LoadExtraMotion(group, str(path)))
            if index < 0:
                return False
            self.versions[key] = index
        native.StartMotion(group, index, 3)
        return True
