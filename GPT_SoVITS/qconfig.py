# 数字小祥项目的统一配置类
import contextlib
import json
import os
import threading
import warnings
from copy import deepcopy
from pathlib import Path

from PyQt5.QtCore import QLockFile
from PyQt5.QtGui import QColor

from typing import Optional

# 去广告
with (contextlib.redirect_stdout(None)):
    from qfluentwidgets import QConfig, OptionsConfigItem, BoolValidator, ConfigItem, OptionsValidator, qconfig, \
    ConfigValidator, RangeConfigItem, RangeValidator


class ThemeColorValidator(ConfigValidator):
    def validate(self, value):
        if not isinstance(value, list):
            return False
        for item in value:
            if not isinstance(item, dict):
                return False
            if "name" not in item or "color" not in item:
                return False
            if not isinstance(item["name"], str) or not isinstance(item["color"], str):
                return False
            if not QColor(item["color"]).isValid():
                return False
        return True


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_MULTI_CHAR_BGM_PATH = str(
    PROJECT_ROOT / "reference_audio" / "small_theater_bgm" / "Normal.mp3"
)


class FileValidator(ConfigValidator):
    """校验配置值是否指向指定类型的现有文件。"""

    def __init__(self, default: str, allowed_suffixes: tuple[str, ...] = ()) -> None:
        self.default = default
        self.allowed_suffixes = tuple(suffix.lower() for suffix in allowed_suffixes)

    def validate(self, value: object) -> bool:
        """检查配置值是否为存在且扩展名受支持的文件路径。"""
        if not isinstance(value, str):
            return False
        path = Path(value)
        if not path.is_file():
            return False
        return not self.allowed_suffixes or path.suffix.lower() in self.allowed_suffixes

    def correct(self, value: object) -> str:
        """无效路径回退到默认文件。"""
        if not self.validate(value):
            return self.default
        return str(value)


class DSakikoConfig(QConfig):
    """
    启动配置与其他使用设置的统一记录参数类

    在启动时，会自动将之前分散在多个文本文件中的配置参数读取进来，转化为新的统一 json。
    """
    # 是否使用 up 的 deepseek API
    use_default_deepseek_api = OptionsConfigItem("llm_setting", "use_default_deepseek_api", True, 
                                                    validator=BoolValidator())

    # 自定义 API Key 相关的配置（依赖库为 litellm）
    # LLM 的模型名称一般为“模型供应商/模型名称”，比如 "openai/gpt-5", "deepseek/deepseek-v4-flash"

    # 这个选项只存储 LLM 提供商字段
    llm_api_provider = ConfigItem("llm_setting", "llm_api_provider", "deepseek")
    # 具体模型名称（例如：gpt-5, gemini-2.5-pro, deepseek-v4-flash）
    # 采用字典形式存储。键为所有可能的 llm_api_provider，但不包含“custom_llm_api_model“，值为对应的模型名称
    llm_api_model = ConfigItem("llm_setting", "llm_api_model", {"deepseek": "deepseek-v4-flash"})
    # API Key
    # 采用字典形式存储。键为所有可能的 llm_api_provider，再加上一个“custom_llm_api_key“，值为对应的 API Key
    llm_api_key = ConfigItem("llm_setting", "llm_api_key", {})

    # 可选的 API Base URL（用于第三方 OpenAI 兼容端点等场景）
    # 采用字典形式存储。键为 llm_api_provider（如 modelscope），值为对应的 base_url。
    # 对于 litellm 自带 provider（openai/deepseek/gemini 等），通常不需要填写。
    llm_api_base_url = ConfigItem("llm_setting", "llm_api_base_url", {"modelscope": "https://api-inference.modelscope.cn/v1"})

    # 是否自定义 API 提供商
    enable_custom_llm_api_provider = OptionsConfigItem("llm_setting", "enable_custom_llm_api_provider", False,
                                                               validator=BoolValidator())
    # 自定义 LLM API 的 URL
    custom_llm_api_url = ConfigItem("llm_setting", "custom_llm_api_url", "")
    # 自定义 LLM API 的模型名称。需要完整的写，比如“deepseek/deepseek-v4-flash” 或者 "openai/gpt-5"
    custom_llm_api_model = ConfigItem("llm_setting", "custom_llm_api_model", "")
    # 自定义 LLM API Key
    custom_llm_api_key = ConfigItem("llm_setting", "custom_llm_api_key", "")
    # 模型温度
    llm_temperature = RangeConfigItem("llm_setting", "llm_temperature", 1.0, validator=RangeValidator(0.0, 2.0))
    # 模型的 top-p
    llm_top_p = RangeConfigItem("llm_setting", "llm_top_p", 1.0, validator=RangeValidator(0.0, 1.0))
    # 是否仍然展示不符合格式要求的模型回复
    display_unformatted_llm_response = OptionsConfigItem("llm_setting", "display_unformatted_llm_response", False,
                                                         validator=BoolValidator())

    # 是否在退出时删除推理音频
    delete_audio_cache_on_exit = OptionsConfigItem("audio_setting", "delete_audio_cache_on_exit", False,
                                                           validator=BoolValidator())
    # 是否使用 fp32 精度推理音频
    # 如果设为 False，则使用 fp16 精度推理
    enable_fp32_inference = OptionsConfigItem("audio_setting", "enable_fp32_inference", True,
                                                      validator=BoolValidator(), restart=True)
    # GPT-SoVITS推理采样步数
    sovits_inference_sampling_steps = OptionsConfigItem("audio_setting", "sovits_inference_sampling_steps", 16,
                                                               validator=OptionsValidator([4, 8, 16, 32]),
                                                               restart=True)
    # 最多加载的语音模型数量
    max_loaded_voice_models = RangeConfigItem("audio_setting", "max_loaded_voice_models", 2,
                                                validator=RangeValidator(1, 5),
                                                restart=True)
    # 是否启用 cuda
    # None：根据系统情况决定；True：强制启用；False：强制禁用
    # 在 ui 中会有特定的设计，让 torch.cuda.is_available = False 的时候无法将此选项选择到 True
    cuda_enabled = OptionsConfigItem("audio_setting", "cuda_enabled", None, 
                                     validator=OptionsValidator([True, False, None]), restart=True)
    # 是否启用 mps（M 系列 MacOS 限定）
    mps_enabled = OptionsConfigItem("audio_setting", "mps_enabled", None, 
                                     validator=OptionsValidator([True, False, None]), restart=True)
    # 角色顺序与信息
    # 内容是默认顺序与信息
    character_order = ConfigItem("character_setting", "character_order", {
        "character_num": 2,
        "character_names": [
            "爱音",
            "祥子"
        ]})

    # 用户自定义人设。内置的默认用户人设由程序创建，不写入配置。
    user_characters = ConfigItem("character_setting", "user_characters", [])
    
    # 角色的默认 live2d json 选择
    l2d_json_paths_dict = ConfigItem("character_setting", "l2d_json_paths_dict", {})
    # 默认的 live2d 背景图片选择
    background_image_path = ConfigItem("character_setting", "background_image_path", "")
    # Live2D 模型布局配置，key 为模型 json 的项目相对路径，value 按 single/theater 场景保存缩放和平移。
    live2d_model_layouts = ConfigItem("character_setting", "live2d_model_layouts", {})

    # 普通聊天侧栏的展示模式：flat 为平铺模式，folded 为按角色折叠模式
    chat_sidebar_mode = OptionsConfigItem("ui_state", "chat_sidebar_mode", "flat",
                                          validator=OptionsValidator(["flat", "folded"]))
    # 普通聊天侧栏中处于展开状态的角色名称列表
    chat_sidebar_expanded_characters = ConfigItem("ui_state", "chat_sidebar_expanded_characters", [])
    # 新建对话弹窗中的用户人设区域是否展开
    user_persona_section_expanded = OptionsConfigItem(
        "ui_state",
        "user_persona_section_expanded",
        True,
        validator=BoolValidator(),
    )
    # 小剧场模式下选择播放的背景音乐
    multi_char_background_music_path = ConfigItem(
        "ui_state",
        "multi_char_background_music_path",
        DEFAULT_MULTI_CHAR_BGM_PATH,
        FileValidator(DEFAULT_MULTI_CHAR_BGM_PATH, (".mp3", ".wav")),
    )

    # 颜色主题默认信息
    theme_color = ConfigItem("theme_color_setting", "theme_color", [
        {
            "name": "千早爱音",
            "color": "#FF8899"
        },
        {
            "name": "长崎素世",
            "color": "#FFDD88"
        },
        {
            "name": "高松灯",
            "color": "#77BBDD"
        },
        {
            "name": "椎名立希",
            "color": "#7777AA"
        },
        {
            "name": "要乐奈",
            "color": "#77DD77"
        },
        {
            "name": "丰川祥子",
            "color": "#7799CC"
        },
        {
            "name": "若叶睦",
            "color": "#779977"
        },
        {
            "name": "三角初华",
            "color": "#BB9955"
        },
        {
            "name": "八幡海玲",
            "color": "#335566"
        },
        {
            "name": "祐天寺若麦",
            "color": "#AA4477"
        },
    ],
     validator=ThemeColorValidator())

    def __init__(self) -> None:
        super().__init__()

        self.lock = QLockFile("../d_sakiko_config.lock")
        self.lock.setStaleLockTime(30_000) # 30s 后自动过期；怎么会有人写磁盘花 30s 呢？
        self._set_without_lock = super().set
        self._transaction_state = threading.local()

    def _transaction_depth(self) -> int:
        """
        获取当前线程进入配置事务的层数。不同线程中，这个值是不一样的，从而保证不同线程内的代码正确。
        """
        return getattr(self._transaction_state, "depth", 0)

    def _set_transaction_depth(self, depth: int) -> None:
        """
        设置当前线程进入配置事务的层数。
        """
        self._transaction_state.depth = depth
    
    def infer_gpu_setting(self):
        """
        填充 cuda_enabled 和 mps_enabled 选项的值。如果这两个值为 None，那么根据机器上是否存在 cuda/mps，将其填充为 True/False。
        如果这两个值是 True/False，那么什么都不会发生。
        """
        if self.cuda_enabled.value is None or self.mps_enabled.value is None: 
            import torch

            if self.cuda_enabled.value is None:
                self.set(self.cuda_enabled, torch.cuda.is_available())
            # Apple M4 芯片上，MPS 的效果和 CPU 推理几乎相同，但占用时间更长
            # 暂时默认不启用 MPS，即使在 MPS 可用的机器上也是如此。如果用户想启用，可以手动打开开关。
            if self.mps_enabled.value is None:
                self.set(self.mps_enabled, False)

    def set(self, item: ConfigItem, value: object, save: bool = True, copy: bool = True) -> None:
        """
        修改一个配置变量的值。为了保持多进程下的同步，每次修改强制写入磁盘。
        """
        if self._transaction_depth() > 0:
            self._set_without_lock(item, value, False, copy)
            return

        try:
            if not self.lock.lock():
                raise RuntimeError("无法获得文件锁")
            self._set_transaction_depth(1)
            self.load(self.file)
            self._set_without_lock(item, value, True, copy)
        finally:
            self._set_transaction_depth(0)
            if self.lock.isLocked():
                self.lock.unlock()

    def __enter__(self) -> "DSakikoConfig":
        depth = self._transaction_depth()
        if depth > 0:
            self._set_transaction_depth(depth + 1)
            return self

        if not self.lock.lock():
            raise RuntimeError("无法获得文件锁")

        try:
            self._set_transaction_depth(1)
            self.load(self.file)
        except Exception:
            self._set_transaction_depth(0)
            self.lock.unlock()
            raise

        return self

    def __exit__(
            self,
            exc_type: Optional[type[BaseException]],
            exc_val: Optional[BaseException],
            exc_tb: object,
    ) -> bool:
        depth = self._transaction_depth()
        if depth > 1:
            self._set_transaction_depth(depth - 1)
            return False

        try:
            if exc_type is None:
                self.save()
            else:
                self.load(self.file)
        finally:
            self._set_transaction_depth(0)
            if self.lock.isLocked():
                self.lock.unlock()

        return False

    def snapshot(self) -> "DSakikoConfigSnapshot":
        """
        从磁盘加载最新配置，并返回一份纯 Python 配置快照。
        """
        if self._transaction_depth() > 0:
            return DSakikoConfigSnapshot(self)

        try:
            if not self.lock.lock():
                raise RuntimeError("无法获得文件锁")
            self._set_transaction_depth(1)
            self.load(self.file)
            return DSakikoConfigSnapshot(self)
        finally:
            self._set_transaction_depth(0)
            if self.lock.isLocked():
                self.lock.unlock()

    def __deepcopy__(self, memo: dict[int, object]) -> "DSakikoConfigSnapshot":
        """
        避免复制 QObject/QLockFile，改为复制一份配置值快照。
        """
        return self.snapshot()

    def reload_from_disk(self) -> None:
        """
        加锁后从磁盘重新加载配置。
        """
        if self._transaction_depth() > 0:
            self.load(self.file)
            return

        try:
            if not self.lock.lock():
                raise RuntimeError("无法获得文件锁")
            self._set_transaction_depth(1)
            self.load(self.file)
        finally:
            self._set_transaction_depth(0)
            if self.lock.isLocked():
                self.lock.unlock()


class ConfigValueSnapshot:
    """
    保存单个配置项的值快照。
    """

    def __init__(self, value: object) -> None:
        try:
            self.value: object = deepcopy(value)
        except Exception:
            self.value = value


class DSakikoConfigSnapshot:
    """
    保存一次对话轮次使用的配置快照。
    """

    def __init__(self, cfg: DSakikoConfig) -> None:
        """
        复制所有 DSakikoConfig 类中的 ConfigItem 作为快照
        """
        for name in dir(cfg.__class__):
            item = getattr(cfg.__class__, name)
            if isinstance(item, ConfigItem):
                setattr(self, name, ConfigValueSnapshot(item.value))


# 这个字典存储了所有可能的“LLM 供应商显示名称”->“实际请求时需要的前缀名称”的映射关系
# 例如 "OpenAI" -> "openai"，"Google Gemini" -> "gemini"，"DeepSeek" -> "deepseek"
# 这个字典中，很多显示名称会被映射为同一存储名称。由于 PROVIDER_FRIENDLY_NAME_MAP 是由这个字典倒序得到的，
# 因此在 PROVIDER_FRIENDLY_NAME_MAP 中，后出现的显示名称会覆盖前面的显示名称。请确保将常用的显示名称放在后面。
PROVIDER_DISPLAY_NAME_MAP = {
    "OpenAI": "openai",

    "Gemini": "gemini",
    # 兼容性问题：之前 API_Choice.json 里用的名字是 "Google"
    "Google": "gemini",
    "Google Gemini": "gemini",

    "Anthropic": "anthropic",

    "DeepSeek": "deepseek",

    "火山引擎": "volcengine",
    "Doubao": "volcengine",
    "doubao": "volcengine",
    "豆包": "volcengine",
    "火山引擎（豆包）": "volcengine",


    "Moonshot": "moonshot",
    "moonshot": "moonshot",
    "月之暗面": "moonshot",
    "Kimi": "moonshot",

    "OpenRouter": "openrouter",

    "VLLM": "vllm",

    "ollama": "ollama",
    "Ollama": "ollama",

    "X AI": "xai",
    "xAI": "xai",

    "Azure": "azure",
    "Azure AI": "azure",

    "HuggingFace": "huggingface",
}


# 倒序上面的字典，得到“实际请求时需要的前缀名称”->“LLM 供应商显示名称”的映射关系
PROVIDER_FRIENDLY_NAME_MAP = {v: k for k, v in PROVIDER_DISPLAY_NAME_MAP.items()}


# 少部分著名提供商。这个列表用于在界面中显示可选的 LLM 供应商。选择和排序均为主观决定，不代表任何立场或者意见（
# 这些供应商列表来自 litellm.constants.py 的 LITELLM_CHAT_PROVIDERS 常量
# 此部分提供商会拥有友好名称，并且在展开前的选择界面中即可选择
FAMOUS_CHAT_PROVIDERS = [
    "deepseek",
    # 请注意：volcengine 是“火山引擎”，实际上是豆包模型
    "volcengine",
    # moonshot 就是“月之暗面”，旗下模型是 kimi
    "moonshot",
    "openai",
    "anthropic",
    "gemini",
    "openrouter",
    "vllm",
    "ollama",
    "xai",
    "azure",
    "huggingface",
]


# 这些 API 提供商会在“更多”菜单中显示且没有友好名称
OTHER_CHAT_PROVIDERS = [
    "cohere",
    "clarifai",
    "replicate",
    "together_ai",
    "helicone",
    "vertex_ai",
    "ai21",
    "baseten",
    "sagemaker",
    "bedrock",
    "nlp_cloud",
    "petals",
    "deepinfra",
    "perplexity",
    "mistral",
    "groq",
    "nvidia_nim",
    "cerebras",
    "codestral",
    "sambanova",
    "cloudflare",
    "fireworks_ai",
    "friendliai",
    "watsonx",
    "triton",
    "predibase",
    "databricks",
    "github",
    "llamafile",
    "lm_studio",
    "galadriel",
    "gradient_ai",
    "novita",
    "meta_llama",
    "featherless_ai",
    "nscale",
    "nebius",
    "dashscope",
    "publicai",
    "heroku",
    "oci",
    "morph",
    "lambda_ai",
    "vercel_ai_gateway",
    "wandb",
    "ovhcloud",
    "lemonade",
    "docker_model_runner",
    "amazon_nova",
]
OTHER_CHAT_PROVIDERS.sort()


# 第三方 OpenAI 兼容端点（固定 base_url，界面只需要模型名+API Key，且不提供模型补全）
# 注意：这里的 id 是本项目内部 provider id，不代表 litellm 的 provider 前缀。
THIRD_PARTY_OPENAI_COMPAT_ENDPOINTS = [
    {
        "id": "modelscope",
        "display_name": "ModelScope（魔搭社区）",
        "base_url": "https://api-inference.modelscope.cn/v1",
        "model_placeholder": "deepseek-ai/DeepSeek-V3.2",
    }
]

THIRD_PARTY_OPENAI_COMPAT_ENDPOINT_MAP = {e["id"]: e for e in THIRD_PARTY_OPENAI_COMPAT_ENDPOINTS}
THIRD_PARTY_OPENAI_COMPAT_PROVIDER_IDS = set(THIRD_PARTY_OPENAI_COMPAT_ENDPOINT_MAP.keys())


def _normalize_legacy_provider_name(provider_name):
    """
    将旧版 dsakiko_config.json 中的供应商名称映射到现行配置系统使用的 provider id。
    """
    if provider_name == "ModelScope":
        return "modelscope"
    return PROVIDER_DISPLAY_NAME_MAP.get(provider_name)


def _is_placeholder_api_key(api_key):
    """
    查看旧配置中的 API Key 是否为占位符 Key。占位符 Key 不应迁移到新配置中。

    占位符 Key 只存在如下四种：
    1. 空的 API Key
    2. sk-xxx...xxx
    3. sk-24xxx
    4. ....
    """
    return not api_key or api_key in {"sk-xxx...xxx", "sk-24xxx", "...."}


def migrate_from_old_config(cfg: DSakikoConfig, enable_warning: bool = False):
    """
    将旧版的分散配置文件迁移到新的统一配置文件中，并且删除旧的配置文件。
    此函数可以被安全的随时调用。如果旧配置文件不存在，则不会进行任何操作。

    :param cfg: 将会根据旧配置文件修改这个 cfg 实例的内容。
    迁移修改会通过 cfg.set 写入；如外层已有配置事务，则在事务退出时统一保存。
    :param enable_warning: 如果读取旧配置文件并迁移后无法删除旧文件，是否打印警告。
    这是因为，如果无法删除旧配置，下次执行程序时此函数还会再次尝试迁移，会导致已有的新配置参数被旧配置覆盖。
    """
    # 路径矫正
    # 将工作目录切换到当前脚本所在目录
    old_cwd = os.getcwd()
    os.chdir(os.path.dirname(os.path.abspath(__file__)))

    # API 配置设置
    # 自定义 OpenAI/Gemini API
    try:
        with open("../API_Choice.json", "r") as f:
            choice = json.load(f)
        
        use_deepseek_api = True

        llm_api_key_dict = cfg.llm_api_key.value
        llm_api_model_dict = cfg.llm_api_model.value
        for one in choice["llm_choose"]:
            key = one["api_key"]
            # 特判：实例的 API Key 是以下两个内容之一；我们将其都视为没有填写。或者，空的字符串也被视为没有填写
            if key in ["sk-xxx...xxx", "...."] or not key:
                continue
            # 其他情况下，记录 API Key。即使这个 key 没有被选择，也记录下来。
            # 根据名称记录到 openai/gemini 字段中
            if one["name"] == "OpenAI":
                llm_api_key_dict["openai"] = key
                llm_api_model_dict["openai"] = one["model"]
            elif one["name"] == "Google":
                llm_api_key_dict["gemini"] = key
                llm_api_model_dict["gemini"] = one["model"]
            # 如果多个 if_choose 都是 True，那么后面的会覆盖前面的，那就这样吧……
            if one["if_choose"]:
                # 记录当前没用 deepseek API
                use_deepseek_api = False
                # 这里宁可崩溃不修改也不能写一个未知的供应商名称进去
                cfg.set(cfg.llm_api_provider, PROVIDER_DISPLAY_NAME_MAP[one["name"]])

        
        # 如果没有设置自定义 API，就代表我们在使用 DeepSeek API
        # 检查 ../API Key.txt 文件，看看里面有没有 DeepSeek 的 API Key
        # 如果有，说明自定义了 API Key；如果没有，说明采用 up 的 API Key
        if use_deepseek_api:
            # 直接把模型改为 DeepSeek 当前推荐的 V4 Flash
            cfg.set(cfg.llm_api_provider, "deepseek")
            llm_api_model_dict["deepseek"] = "deepseek-v4-flash"
            with open("../API Key.txt", "r") as f:
                api_key = f.read().strip()
                if not api_key:
                    cfg.set(cfg.use_default_deepseek_api, True)
                else:
                    # 记录自定义 DeepSeek API Key
                    llm_api_key_dict["deepseek"] = api_key
                    cfg.set(cfg.use_default_deepseek_api, False)
        else:
            # 如果不是用 DeepSeek API，就一定不是用 up 的 DeepSeek API
            cfg.set(cfg.use_default_deepseek_api, False)

        # 删除旧文件
        try:
            os.remove("../API_Choice.json")
        except OSError:
            if enable_warning:
                warnings.warn("无法删除旧的 API_Choice.json 配置文件。这可能导致新配置被旧配置反向覆盖，建议手动删除该文件。")
        try:
            os.remove("../API Key.txt")
        except OSError:
            if enable_warning:
                warnings.warn("无法删除旧的 API Key.txt 配置文件。这可能导致新配置被旧配置反向覆盖，建议手动删除该文件。")

    except Exception:
        pass

    # 音频相关设置
    # 是否启用 FP16 推理
    try:
        with open("../is_fp32.txt", "r") as f:
            use_fp16_str = f.read().strip()
            # 这个默认为 True，即只在下列少数条件下为 False
            if use_fp16_str.lower() == "false" or (use_fp16_str.isdigit() and int(use_fp16_str) == 0):
                cfg.set(cfg.enable_fp32_inference, False)
            else:
                cfg.set(cfg.enable_fp32_inference, True)
        # 删除文件
        try:
            os.remove("../is_fp32.txt")
        except OSError:
            if enable_warning:
                warnings.warn("无法删除旧的 is_fp32.txt 配置文件。这可能导致新配置被旧配置反向覆盖，建议手动删除该文件。")
    except Exception:
        pass
    # 是否在退出时删除推理音频
    try:
        with open("../if_delete_audio_cache.txt", "r") as f:
            delete_cache_str = f.read().strip()
            # 如果写的内容为 "true"（不区分大小写）或者一个非零数字，就启用删除缓存
            if delete_cache_str.lower() == "true" or (delete_cache_str.isdigit() and int(delete_cache_str) != 0):
                cfg.set(cfg.delete_audio_cache_on_exit, True)
            else:
                cfg.set(cfg.delete_audio_cache_on_exit, False)
        
        # 删除文件
        try:
            os.remove("../if_delete_audio_cache.txt")
        except OSError:
            if enable_warning:
                warnings.warn("无法删除旧的 if_delete_audio_cache.txt 配置文件。这可能导致新配置被旧配置反向覆盖，建议手动删除该文件。")
    except Exception:
        pass
    # 推理步数
    try:
        with open("../reference_audio/GSV_sample_rate.txt", "r") as f:
            steps_str = f.read().strip()
            if steps_str.isdigit():
                steps = int(steps_str)
                # 如果内容是正确的 4\8\16\32 之一，就设置
                if steps in cfg.sovits_inference_sampling_steps.options:
                    cfg.set(cfg.sovits_inference_sampling_steps, steps)
        
        # 删除文件
        try:
            os.remove("../reference_audio/GSV_sample_rate.txt")
        except OSError:
            if enable_warning:
                warnings.warn("无法删除旧的 GSV_sample_rate.txt 配置文件。这可能导致新配置被旧配置反向覆盖，建议手动删除该文件。")
    except Exception:
        pass

    # 角色顺序与信息
    try:
        with open("../reference_audio/character_order.json", "r", encoding="utf-8") as f:
            character_order = json.load(f)
            cfg.set(cfg.character_order, character_order)
        
        # 删除文件
        try:
            os.remove("../reference_audio/character_order.json")
        except OSError:
            if enable_warning:
                warnings.warn("无法删除旧的 character_order.json 配置文件。这可能导致新配置被旧配置反向覆盖，建议手动删除该文件。")

    except Exception:
        pass

    # 把工作目录换回去（以防万一）
    os.chdir(old_cwd)


def migrate_from_legacy_d_sakiko_config(cfg: DSakikoConfig, enable_warning: bool = False):
    """
    将另一套旧版统一配置文件 ../dsakiko_config.json 迁移到当前使用的 ../d_sakiko_config.json。

    此函数只负责迁移新旧统一配置文件之间的格式差异；迁移成功后会更新 cfg 并删除旧的 dsakiko_config.json。
    如果旧配置文件不存在，则不会进行任何操作。
    """
    old_cwd = os.getcwd()
    os.chdir(os.path.dirname(os.path.abspath(__file__)))

    legacy_config_path = "../dsakiko_config.json"
    if not os.path.exists(legacy_config_path):
        os.chdir(old_cwd)
        return

    try:
        with open(legacy_config_path, "r", encoding="utf-8") as f:
            legacy_cfg = json.load(f)
    except Exception:
        os.chdir(old_cwd)
        return

    if not isinstance(legacy_cfg, dict):
        warnings.warn(
            "[Warning]旧版 dsakiko_config.json 的顶层结构不是 JSON 对象，跳过本次迁移。"
        )
        os.chdir(old_cwd)
        return

    try:
        legacy_character_setting = legacy_cfg.get("character_setting", {})
        if isinstance(legacy_character_setting, dict):
            character_order = legacy_character_setting.get("character_order")
            if (
                    isinstance(character_order, dict)
                    and isinstance(character_order.get("character_names"), list)
            ):
                character_names = [str(name) for name in character_order["character_names"]]
                cfg.set(
                    cfg.character_order,
                    {
                        "character_num": len(character_names),
                        "character_names": character_names,
                    },
                )

        legacy_llm_setting = legacy_cfg.get("llm_setting", {})
        if isinstance(legacy_llm_setting, dict):
            cfg.set(cfg.enable_custom_llm_api_provider, False)
            llm_api_key_dict = dict(cfg.llm_api_key.value)
            llm_api_model_dict = dict(cfg.llm_api_model.value)
            llm_api_base_url_dict = dict(cfg.llm_api_base_url.value)

            is_deepseek = bool(legacy_llm_setting.get("is_deepseek", True))
            if is_deepseek:
                cfg.set(cfg.llm_api_provider, "deepseek")
                llm_api_model_dict["deepseek"] = "deepseek-v4-flash"
                deepseek_key = legacy_llm_setting.get("deepseek_key", "use_api_of_up")
                if _is_placeholder_api_key(deepseek_key) or deepseek_key == "use_api_of_up":
                    cfg.set(cfg.use_default_deepseek_api, True)
                else:
                    cfg.set(cfg.use_default_deepseek_api, False)
                    llm_api_key_dict["deepseek"] = deepseek_key
            else:
                cfg.set(cfg.use_default_deepseek_api, False)
                selected_provider_id = None
                other_providers = legacy_llm_setting.get("other_provider", [])
                if not isinstance(other_providers, list):
                    other_providers = []

                for provider_info in other_providers:
                    if not isinstance(provider_info, dict):
                        continue

                    provider_name = provider_info.get("name", "")
                    provider_id = _normalize_legacy_provider_name(provider_name)
                    if provider_id is None:
                        continue

                    model_name = provider_info.get("model", "")
                    api_key = provider_info.get("api_key", "")
                    base_url = provider_info.get("base_url", "")

                    if model_name:
                        llm_api_model_dict[provider_id] = model_name
                    if not _is_placeholder_api_key(api_key):
                        llm_api_key_dict[provider_id] = api_key
                    if base_url:
                        # openai 和 gemini 这两个预置的 API 就不保存了…反正是可以直接用的
                        if provider_id not in ["openai", "gemini"]:
                            llm_api_base_url_dict[provider_id] = base_url

                    if provider_info.get("if_choose", False):
                        if selected_provider_id is not None and selected_provider_id != provider_id:
                            warnings.warn(
                                "[Warning]旧版 dsakiko_config.json 中发现多个被选中的大模型供应商，"
                                f"将采用靠后的供应商 {provider_name}。"
                            )
                        selected_provider_id = provider_id

                # 如果没有找到被选中的模型，默认采用 DeepSeek V4 Flash 模型
                if selected_provider_id is None:
                    cfg.set(cfg.use_default_deepseek_api, True)
                    cfg.set(cfg.llm_api_provider, "deepseek")
                    llm_api_model_dict["deepseek"] = "deepseek-v4-flash"
                else:
                    cfg.set(cfg.llm_api_provider, selected_provider_id)

            cfg.set(cfg.llm_api_key, llm_api_key_dict)
            cfg.set(cfg.llm_api_model, llm_api_model_dict)
            cfg.set(cfg.llm_api_base_url, llm_api_base_url_dict)

        legacy_audio_setting = legacy_cfg.get("audio_setting", {})
        if isinstance(legacy_audio_setting, dict):
            if "if_delete_audio_cache" in legacy_audio_setting:
                cfg.set(cfg.delete_audio_cache_on_exit, bool(legacy_audio_setting["if_delete_audio_cache"]))

            if "enable_fp16_inference" in legacy_audio_setting:
                cfg.set(cfg.enable_fp32_inference, not bool(legacy_audio_setting["enable_fp16_inference"]))

            steps = legacy_audio_setting.get("sovits_inference_sampling_steps")
            if steps in cfg.sovits_inference_sampling_steps.options:
                cfg.set(cfg.sovits_inference_sampling_steps, steps)
            elif steps is not None:
                cfg.set(cfg.sovits_inference_sampling_steps, 16)
                warnings.warn(
                    "[Warning]旧版 dsakiko_config.json 中的 sovits_inference_sampling_steps 非法，"
                    "已回退为 16。"
                )

        try:
            os.remove(legacy_config_path)
        except OSError:
            if enable_warning:
                warnings.warn(
                    "[Warning]无法删除旧的 dsakiko_config.json 配置文件。"
                    "这可能导致新配置被旧配置反向覆盖，建议手动删除该文件。"
                )
    finally:
        os.chdir(old_cwd)


def normalize_deepseek_model_config(cfg: DSakikoConfig):
    """
    将旧版 DeepSeek 模型别名迁移到当前官方 V4 模型名。
    """
    DEEPSEEK_DEPRECATED_MODEL_ALIASES = {
        "deepseek-chat": "deepseek-v4-flash",
        "deepseek-reasoner": "deepseek-v4-flash",
    }
    models = dict(cfg.llm_api_model.value)
    current = models.get("deepseek")
    if isinstance(current, str):
        normalized = current.strip()
        normalized = DEEPSEEK_DEPRECATED_MODEL_ALIASES.get(normalized, normalized)
        if normalized != current:
            models["deepseek"] = normalized
            cfg.set(cfg.llm_api_model, models)


os.chdir(os.path.dirname(os.path.abspath(__file__)))

# 全局唯一配置实例
d_sakiko_config = DSakikoConfig()
# qfluentwidgets 的部分控件会直接调用全局 qconfig.set，而不是当前配置实例的 set。
# 只覆盖这个全局实例，避免修改 QConfig 类影响其他配置对象。
qconfig.set = d_sakiko_config.set
# 手动设置一个默认值（可以被其他的覆盖）
d_sakiko_config._set_without_lock(d_sakiko_config.themeColor, QColor("#7799CC"), save=False)
qconfig.load("../d_sakiko_config.json", d_sakiko_config)
with d_sakiko_config:
    d_sakiko_config.infer_gpu_setting()

    # 尝试从旧配置文件迁移配置
    migrate_from_old_config(d_sakiko_config)
    # 尝试从另一套旧版统一配置文件迁移配置
    migrate_from_legacy_d_sakiko_config(d_sakiko_config, enable_warning=True)
    normalize_deepseek_model_config(d_sakiko_config)


def create_d_sakiko_config_snapshot() -> DSakikoConfigSnapshot:
    """
    创建一份最新的 d_sakiko_config 纯数据快照。
    """
    return d_sakiko_config.snapshot()
