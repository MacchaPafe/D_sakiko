"""下载器展示配置；不改变运行时角色所属乐队。"""
from dataclasses import dataclass
from pathlib import Path

from ui_constants import char_info_json, DOWNLOADER_OTHERS_CHARACTER_IDS

APP_ROOT = Path(__file__).resolve().parents[2]
PROJECT_ROOT = APP_ROOT.parent
CACHE_ROOT = APP_ROOT / ".model_download_cache" / "hosted"
CHARACTERS = {info["romaji"]: dict(info, display_name=name) for name, info in char_info_json.items()}

# (资源目录标识、展示名称、成员顺序)。人数不由界面假设。
BANDS = (
    ("poppinparty", "Poppin’Party", ("kasumi", "tae", "rimi", "saaya", "arisa")),
    ("afterglow", "Afterglow", ("ran", "moca", "himari", "tomoe", "tsugumi")),
    ("pastel-palettes", "Pastel＊Palettes", ("aya", "hina", "chisato", "mami", "eve")),
    ("roselia", "Roselia", ("yukina", "sayo", "lisa", "ako", "rinko")),
    ("hello-happy-world", "Hello, Happy World!", ("kokoro", "kaoru", "hagumi", "kanon", "misaki")),
    ("morfonica", "Morfonica", ("mashiro", "toko", "nanami", "tsukushi", "rui")),
    ("raise-a-suilen", "RAISE A SUILEN", ("layer", "lock", "masking", "pareo", "chuchu")),
    ("mygo", "MyGO!!!!!", ("tomori", "anon", "rana", "soyo", "taki")),
    ("avemujica", "Ave Mujica", ("uika", "mutsumi", "umiri", "nyamu", "sakiko")),
    ("yumemita", "夢限大MewType", ("arale", "nonoka", "ritsu", "miyako", "yuno")),
    ("millsage", "millsage", ("hotaru", "natsume", "nagi", "mahoro", "houka")),
    ("ikka-dumb-rock", "一家Dumb Rock!", ("raika", "miku", "yomogi", "chieri", "shizuku")),
    ("others", "Others", DOWNLOADER_OTHERS_CHARACTER_IDS),
)

# 按角色标识维护担当，避免依赖成员排序猜测乐器；配角未确认则不展示。
# 新乐队资料：https://bang-dream.com/artist/millsage/izawa-natsume/
# https://bang-dream.com/artist/ikka-dumb-rock/suga-raika/
# https://bm-echoes.com/creators/yumemita/
CHARACTER_ROLES = {
    **dict.fromkeys(("kasumi", "ran", "uika", "raika", "miku"), "Vo. / Gt."),
    **dict.fromkeys(("aya", "yukina", "kokoro", "mashiro", "tomori", "arale"), "Vo."),
    **dict.fromkeys(("tae", "moca", "hina", "sayo", "kaoru", "toko", "lock",
                     "anon", "rana", "mutsumi", "nonoka", "ritsu", "natsume", "nagi"), "Gt."),
    **dict.fromkeys(("rimi", "himari", "chisato", "lisa", "hagumi", "nanami", "soyo",
                     "umiri", "mahoro", "yomogi"), "Ba."),
    **dict.fromkeys(("saaya", "tomoe", "mami", "ako", "kanon", "tsukushi", "masking",
                     "taki", "nyamu", "houka", "chieri"), "Dr."),
    **dict.fromkeys(("arisa", "tsugumi", "eve", "rinko", "pareo", "sakiko", "miyako", "shizuku"), "Key."),
    "rui": "Vn.", "layer": "Vo. / Ba.", "hotaru": "Vo. / Key.",
    "misaki": "DJ", "chuchu": "DJ", "yuno": "DJ / Mp.",
}


@dataclass(frozen=True)
class Selection:
    mode: str = "new"
    source: str = ""
    target_id: str = ""
    target_name: str = ""


def destination(resource, zip_root):
    """根据资源类型确定保存目录；模型 ZIP 与持久图片分开存放。"""
    if resource.kind == "model":
        return Path(zip_root) / resource.character / resource.model_id
    if resource.kind == "background":
        return PROJECT_ROOT / "live2d_related"
    return APP_ROOT / "assets/char_headprof/webui_chat_mode_avatars" / resource.character
