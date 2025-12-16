# 来源：https://github.com/zhiyiYo/PyQt-Fluent-Widgets/blob/0e83ba1ef2e518dd4d66f0924a24be4d29a2cdbc/qfluentwidgets/common/config.py
# 原仓库遵守 GPL-3.0 许可证，版权归 zhiyiYo 所有。
# 因为本项目同样采用 GPL-3.0 许可证，可以直接使用该代码。
import json
import os
from copy import deepcopy
from enum import Enum
from pathlib import Path
from typing import List
import warnings

from PyQt5.QtCore import QObject, pyqtSignal
from PyQt5.QtGui import QColor


class ConfigValidator:
    """ Config validator """

    def validate(self, value) -> bool:
        """ Verify whether the value is legal """
        return True

    def correct(self, value):
        """ correct illegal value """
        return value


class RangeValidator(ConfigValidator):
    """ Range validator """

    def __init__(self, min, max):
        self.min = min
        self.max = max
        self.range = (min, max)

    def validate(self, value):
        return self.min <= value <= self.max

    def correct(self, value):
        return min(max(self.min, value), self.max)


class OptionsValidator(ConfigValidator):
    """ Options validator """

    def __init__(self, options):
        if not options:
            raise ValueError("The `options` can't be empty.")

        if isinstance(options, Enum):
            options = options._member_map_.values()

        self.options = list(options)

    def validate(self, value) -> bool:
        """
        Verify whether the value is legal by checking if it is in the options list
        """
        return value in self.options

    def correct(self, value):
        return value if self.validate(value) else self.options[0]


class BoolValidator(OptionsValidator):
    """ Boolean validator, only allow True and False option values """

    def __init__(self):
        super().__init__([True, False])


class FolderValidator(ConfigValidator):
    """ Folder validator """

    def validate(self, value):
        return Path(value).exists()

    def correct(self, value):
        path = Path(value)
        path.mkdir(exist_ok=True, parents=True)
        return str(path.absolute()).replace("\\", "/")


class FolderListValidator(ConfigValidator):
    """ Folder list validator """

    def validate(self, value):
        return all(Path(i).exists() for i in value)

    def correct(self, value: List[str]):
        folders = []
        for folder in value:
            path = Path(folder)
            if path.exists():
                folders.append(str(path.absolute()).replace("\\", "/"))

        return folders


class ColorValidator(ConfigValidator):
    """ RGB color validator """

    def __init__(self, default):
        self.default = QColor(default)

    def validate(self, value) -> bool:
        try:
            return QColor(value).isValid()
        except:
            return False

    def correct(self, value):
        return QColor(value) if self.validate(value) else self.default


class ConfigSerializer:
    """ Config serializer """

    def serialize(self, value):
        """ serialize config value """
        return value

    def deserialize(self, value):
        """ deserialize config from config file's value """
        return value


class EnumSerializer(ConfigSerializer):
    """ enumeration class serializer """

    def __init__(self, enumClass):
        self.enumClass = enumClass

    def serialize(self, value):
        return value.value

    def deserialize(self, value):
        return self.enumClass(value)


class ColorSerializer(ConfigSerializer):
    """ QColor serializer """

    def serialize(self, value: QColor):
        return value.name(QColor.HexArgb)

    def deserialize(self, value):
        if isinstance(value, list):
            return QColor(*value)

        return QColor(value)


class ConfigItem(QObject):
    """ Config item """

    valueChanged = pyqtSignal(object)

    def __init__(self, group: str, name: str, default, validator: ConfigValidator | None = None, serializer: ConfigSerializer | None = None, restart: bool = False):
        """
        Parameters
        ----------
        group: str
            config group name

        name: str
            config item name, can be empty

        default:
            default value

        options: list
            options value

        serializer: ConfigSerializer
            config serializer

        restart: bool
            whether to restart the application after updating value
        """
        super().__init__()
        self.group = group
        self.name = name
        self.validator = validator or ConfigValidator()
        self.serializer = serializer or ConfigSerializer()
        self.__value = default
        self.value = default
        self.restart = restart
        self.defaultValue = self.validator.correct(default)

    @property
    def value(self):
        """ get the value of config item """
        return self.__value

    @value.setter
    def value(self, v):
        v = self.validator.correct(v)
        ov = self.__value
        self.__value = v
        if ov != v:
            self.valueChanged.emit(v)

    @property
    def key(self):
        """ get the config key separated by `.` """
        return self.group+"."+self.name if self.name else self.group

    def __str__(self):
        return f'{self.__class__.__name__}[value={self.value}]'

    def serialize(self):
        return self.serializer.serialize(self.value)

    def deserializeFrom(self, value):
        self.value = self.serializer.deserialize(value)


class RangeConfigItem(ConfigItem):
    """ Config item of range """

    @property
    def range(self):
        """ get the available range of config """
        return self.validator.range

    def __str__(self):
        return f'{self.__class__.__name__}[range={self.range}, value={self.value}]'


class OptionsConfigItem(ConfigItem):
    """ Config item with options """

    @property
    def options(self):
        return self.validator.options

    def __str__(self):
        return f'{self.__class__.__name__}[options={self.options}, value={self.value}]'


class ColorConfigItem(ConfigItem):
    """ Color config item """

    def __init__(self, group, name, default, restart=False):
        super().__init__(group, name, QColor(default), ColorValidator(default),
                         ColorSerializer(), restart)

    def __str__(self):
        return f'{self.__class__.__name__}[value={self.value.name()}]'
    

def exceptionHandler(*default):
    """ decorator for exception handling

    Parameters
    ----------
    *default:
        the default value returned when an exception occurs
    """

    def outer(func):

        def inner(*args, **kwargs):
            try:
                return func(*args, **kwargs)
            except BaseException as e:
                value = deepcopy(default)
                if len(value) == 0:
                    return None
                elif len(value) == 1:
                    return value[0]

                return value

        return inner

    return outer


class QConfig(QObject):
    """ Config of app """
    # 当修改了一个被标记为 restart=True（需要重启后生效）的配置项时，发出该信号
    appRestartSignal = pyqtSignal()

    def __init__(self):
        super().__init__()
        self.file = Path("../d_sakiko_config.json")
        self._cfg = self

    def get(self, item):
        """ get the value of config item """
        return item.value

    def set(self, item, value, save=True, copy=True):
        """ set the value of config item

        Parameters
        ----------
        item: ConfigItem
            config item

        value:
            the new value of config item

        save: bool
            whether to save the change to config file

        copy: bool
            whether to deep copy the new value
        """
        if item.value == value:
            return

        # deepcopy new value
        try:
            item.value = deepcopy(value) if copy else value
        except:
            item.value = value

        if save:
            self.save()
        
        if item.restart:
            self.appRestartSignal.emit()

    def toDict(self, serialize=True):
        """ convert config items to `dict` """
        items = {}
        for name in dir(self._cfg.__class__):
            item = getattr(self._cfg.__class__, name)
            if not isinstance(item, ConfigItem):
                continue

            value = item.serialize() if serialize else item.value
            if not items.get(item.group):
                if not item.name:
                    items[item.group] = value
                else:
                    items[item.group] = {}

            if item.name:
                items[item.group][item.name] = value

        return items

    def save(self):
        """ save config """
        self._cfg.file.parent.mkdir(parents=True, exist_ok=True)
        with open(self._cfg.file, "w", encoding="utf-8") as f:
            json.dump(self._cfg.toDict(), f, ensure_ascii=False, indent=4)

    @exceptionHandler()
    def load(self, file=None, config=None):
        """ load config

        Parameters
        ----------
        file: str or Path
            the path of json config file

        config: Config
            config object to be initialized
        """
        if isinstance(config, QConfig):
            self._cfg = config

        if isinstance(file, (str, Path)):
            self._cfg.file = Path(file)

        try:
            with open(self._cfg.file, encoding="utf-8") as f:
                cfg = json.load(f)
        except Exception:
            cfg = {}

        # map config items'key to item
        items = {}
        for name in dir(self._cfg.__class__):
            item = getattr(self._cfg.__class__, name)
            if isinstance(item, ConfigItem):
                items[item.key] = item

        # update the value of config item
        for k, v in cfg.items():
            if not isinstance(v, dict) and items.get(k) is not None:
                items[k].deserializeFrom(v)
            elif isinstance(v, dict):
                for key, value in v.items():
                    key = k + "." + key
                    if items.get(key) is not None:
                        items[key].deserializeFrom(value)


class DSakikoConfig(QConfig):
    """
    启动配置与其他使用设置的统一记录参数类

    在启动时，会自动将之前分散在多个文本文件中的配置参数读取进来，转化为新的统一 json。
    """
    # 是否使用 up 的 deepseek API
    use_default_deepseek_api = OptionsConfigItem("llm_setting", "use_default_deepseek_api", True, 
                                                    validator=BoolValidator())

    # 自定义 API Key 相关的配置（依赖库为 litellm）
    # LLM 的模型名称一般为“模型供应商/模型名称”，比如 "openai/gpt-5", "deepseek/deepseek-chat"

    # 直接受到 litellm 支持的 LLM 提供商（例如：OpenAI, Google, Anthropic, DeepSeek）
    # 这个选项只存储 LLM 提供商字段
    llm_api_provider = ConfigItem("llm_setting", "llm_api_provider", "deepseek")
    # 具体模型名称（例如：gpt-5, gemini-2.5-pro, deepseek-chat）
    # 采用字典形式存储。键为所有可能的 llm_api_provider，但不包含“custom_llm_api_model“，值为对应的模型名称
    llm_api_model = ConfigItem("llm_setting", "llm_api_model", {"deepseek": "deepseek-chat"})
    # API Key
    # 采用字典形式存储。键为所有可能的 llm_api_provider，再加上一个“custom_llm_api_key“，值为对应的 API Key
    llm_api_key = ConfigItem("llm_setting", "llm_api_key", {})

    # 是否自定义 API 提供商
    enable_custom_llm_api_provider = OptionsConfigItem("llm_setting", "enable_custom_llm_api_provider", False,
                                                               validator=BoolValidator())
    # 自定义 LLM API 的 URL
    custom_llm_api_url = ConfigItem("llm_setting", "custom_llm_api_url", "")
    # 自定义 LLM API 的模型名称。需要完整的写，比如“deepseek/deepseek-chat” 或者 "openai/gpt-5"
    custom_llm_api_model = ConfigItem("llm_setting", "custom_llm_api_model", "")
    # 自定义 LLM API Key
    custom_llm_api_key = ConfigItem("llm_setting", "custom_llm_api_key", "")

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
    # 角色顺序与信息
    # 内容是默认顺序与信息
    character_order = ConfigItem("character_setting", "character_order", {
        "character_num": 2,
        "character_names": [
            "爱音",
            "祥子"
        ]})


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


def migrate_from_old_config(cfg: DSakikoConfig, enable_warning: bool = False):
    """
    将旧版的分散配置文件迁移到新的统一配置文件中，并且删除旧的配置文件。
    此函数可以被安全的随时调用。如果旧配置文件不存在，则不会进行任何操作。

    :param cfg: 将会根据旧配置文件修改这个 cfg 实例的内容。
    迁移后，配置会被自动保存一次。
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
                cfg.llm_api_provider.value = PROVIDER_DISPLAY_NAME_MAP[one["name"]]

        
        # 如果没有设置自定义 API，就代表我们在使用 DeepSeek API
        # 检查 ../API Key.txt 文件，看看里面有没有 DeepSeek 的 API Key
        # 如果有，说明自定义了 API Key；如果没有，说明采用 up 的 API Key
        if use_deepseek_api:
            # 直接把模型改为 deepseek/deepseek-chat
            cfg.llm_api_provider.value = "deepseek"
            llm_api_model_dict["deepseek"] = "deepseek-chat"
            with open("../API Key.txt", "r") as f:
                api_key = f.read().strip()
                if not api_key:
                    cfg.use_default_deepseek_api.value = True
                else:
                    # 记录自定义 DeepSeek API Key
                    llm_api_key_dict["deepseek"] = api_key
                    cfg.use_default_deepseek_api.value = False
        else:
            # 如果不是用 DeepSeek API，就一定不是用 up 的 DeepSeek API
            cfg.use_default_deepseek_api.value = False

        # 删除旧文件
        try:
            os.remove("../API_Choice.json")
        except Exception:
            if enable_warning:
                warnings.warn("无法删除旧的 API_Choice.json 配置文件。这可能导致新配置被旧配置反向覆盖，建议手动删除该文件。")
        try:
            os.remove("../API Key.txt")
        except Exception:
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
                cfg.enable_fp32_inference.value = False
            else:
                cfg.enable_fp32_inference.value = True
        # 删除文件
        try:
            os.remove("../is_fp32.txt")
        except Exception:
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
                cfg.delete_audio_cache_on_exit.value = True
            else:
                cfg.delete_audio_cache_on_exit.value = False
        
        # 删除文件
        try:
            os.remove("../if_delete_audio_cache.txt")
        except Exception:
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
                    cfg.sovits_inference_sampling_steps.value = steps
        
        # 删除文件
        try:
            os.remove("../reference_audio/GSV_sample_rate.txt")
        except Exception:
            if enable_warning:
                warnings.warn("无法删除旧的 GSV_sample_rate.txt 配置文件。这可能导致新配置被旧配置反向覆盖，建议手动删除该文件。")
    except Exception:
        pass

    # 角色顺序与信息
    try:
        with open("../reference_audio/character_order.json", "r", encoding="utf-8") as f:
            character_order = json.load(f)
            cfg.character_order.value = character_order
        
        # 删除文件
        try:
            os.remove("../reference_audio/character_order.json")
        except Exception:
            if enable_warning:
                warnings.warn("无法删除旧的 character_order.json 配置文件。这可能导致新配置被旧配置反向覆盖，建议手动删除该文件。")

    except Exception:
        pass

    # 保存修改
    cfg.save()
    # 把工作目录换回去（以防万一）
    os.chdir(old_cwd)


# 全局唯一配置实例
d_sakiko_config = DSakikoConfig()
d_sakiko_config.load()
# 尝试从旧配置文件迁移配置
migrate_from_old_config(d_sakiko_config)
