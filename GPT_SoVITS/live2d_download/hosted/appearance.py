"""复用项目对话框样式，并将尺寸统一换算为当前屏幕的逻辑像素。"""
import re
from dataclasses import dataclass
from PyQt5.QtGui import QColor
from ui_constants import dialogWindowDefaultCss

BAND_COLORS = ("#E95B88", "#D85963", "#64BBA8", "#7772BC", "#DBAD39",
               "#729CDA", "#56B5BB", "#518EB5", "#A95370", "#D48BBD",
               "#8973AD", "#669D74", "#7799CC")


# 各页面样式集中维护；页面规则仅应用到对应页面，尺寸统一按屏幕换算。
COMMON_STYLE = """
QLabel#modeHint {color:#8794A8; font-size:14px;}
/* 透明顶栏和统一玻璃胶囊；尺寸仍由 ScreenMetrics 换算。 */
QFrame#topNavContainer {
    background:transparent; border:0; border-bottom:1px solid rgba(0,0,0,0.04);
    min-height:42px; max-height:42px; padding:7px 12px 4px 12px;
}
QFrame#mainTabTrack, QFrame#subModeTrack {
    background:rgba(255,255,255,0.55); border:1px solid rgba(255,255,255,0.7);
    border-radius:12px; padding:3px;
}
QPushButton[topTab="true"], QPushButton[subMode="true"] {
    background:transparent; border:none; border-radius:9px;
    font-family:'Microsoft YaHei',sans-serif; font-size:13px; font-weight:600;
    color:#637089; padding:5px 16px; min-height:24px;
}
QPushButton[topTab="true"]:hover, QPushButton[subMode="true"]:hover {
    color:#2F394E; background:rgba(255,255,255,0.5);
}
QPushButton[topTab="true"]:checked {background:#FFFFFF; color:#2D364D; font-weight:700;}
QPushButton[subMode="true"]:checked {background:#7799CC; color:#FFFFFF; font-weight:700;}
QPushButton[topTab="true"]:disabled, QPushButton[subMode="true"]:disabled {color:#A9B3C4;}
QWidget {font-size:18px;}
        QWidget[themeSurface="true"] {background:transparent;}
        QToolButton:checked {background-color:rgba(119,153,204,0.16); border:2px solid #7799CC;}
        QComboBox, QLineEdit {background:white; color:#7799CC; border:1px solid #D0D0D0;
            border-radius:4px; padding:7px 12px;}
        QScrollBar:horizontal {background:#CAD4E4; height:6px; border:0;}
        QScrollBar::handle:horizontal {background:#7799CC; min-width:24px; border-radius:3px;}
        QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {width:0;}
"""

HOME_STYLE = """
/* 主标题：深冷紫+字间距 */
QLabel#downloadTitle {
    font-family: 'Microsoft YaHei';
    font-size: 32px;
    font-weight: 700;
    color: #2F374E;
    letter-spacing: 2px;
}

/* 副标题：轻盈灰 */
QLabel#downloadSubtitle {
    font-size: 13px;
    color: #7B859D;
    letter-spacing: 0.5px;
}

/* 2×2 单选功能卡片 */
QToolButton#homeSelectCard {
    background: rgba(255, 255, 255, 0.95);
    border: 1.5px solid #DFE5F0;
    border-radius: 16px;
    padding: 16px 20px;
    min-width: 220px;
    min-height: 100px;
    color: #333A4E;
}

QToolButton#homeSelectCard:hover {
    background: #FFFFFF;
    border-color: #7799CC;
    border-width: 2px;
}

/* 选中态：祥子冷灰紫描边与轻微内发光 */
QToolButton#homeSelectCard:checked {
    background: #F6F8FD;
    border: 2px solid #5E6CA3;
    color: #2F374E;
}

/* 右下角继续按钮 */
QPushButton#homeNextBtn {
    background: #5E6CA3;
    color: #FFFFFF;
    border: none;
    border-radius: 12px;
    font-size: 15px;
    font-weight: 600;
    padding: 10px 28px;
}

QPushButton#homeNextBtn:hover {
    background: #515E8F;
}

QPushButton#homeNextBtn:pressed {
    background: #424D7A;
}

QPushButton#homeNextBtn:disabled {
    background: #E0E4ED;
    color: #A3ACB9;
}
"""

CHARACTERS_STYLE = """
QLabel#footerBandLogo {background:transparent; border:0; padding:0;}
QPushButton#resourceCredit, QPushButton#resourceCredit:hover, QPushButton#resourceCredit:pressed {background:transparent; border:0; color:#9298A2; font-size:12px; font-weight:400; padding:2px 8px;}
QComboBox {border-radius:10px; padding:9px 36px 9px 14px;}
        QComboBox:hover, QComboBox:focus {border:1px solid #7799CC;}
        QComboBox::drop-down {border:0; width:32px; background:transparent;}
        QComboBox::down-arrow {image:none; border:0;}
        QComboBox QAbstractItemView {background:white; border:1px solid #CAD4E4;
            selection-background-color:#E5EBF5; selection-color:#7799CC; padding:6px;}
        QToolButton#carouselArrow {background:rgba(255,255,255,0.9); border:1px solid #CAD4E4;
            border-radius:16px; font-size:32px; color:#7799CC;}
        QToolButton#carouselArrow:hover {background:white; border-color:#7799CC;}
        QToolButton#bandTile {background:rgba(255,255,255,150); border:1px solid #DFE5EF;
            border-bottom:3px solid #DDE3EE; border-radius:12px; padding:4px;}
        QToolButton#bandTile:hover {background:white; border-color:#ACBCD7;}
        QToolButton#bandTile:checked {background:white; border-width:2px;
            border-bottom-width:4px; padding-bottom:2px;}
        QToolButton#bandTile:pressed {background:#E9EEF7; padding-top:7px; padding-bottom:1px;}
        QToolButton#otherCharacters {background:transparent; border:0; padding:4px; color:#8798B2;}
        QToolButton#otherCharacters:hover {background:transparent; border:0; color:#536F9B;}
        QToolButton#otherCharacters:checked {background:transparent; border:0; color:#536F9B; font-weight:600;}
        QPushButton#primaryDownload {background:#7799CC; color:white; border:1px solid #7799CC;
            border-bottom:3px solid #5E80B4; border-radius:14px; padding:12px 32px;
            min-width:210px; max-width:320px; font-weight:600;}
        QPushButton#primaryDownload:hover {background:#86A6D5;}
        QPushButton#primaryDownload:pressed {background:#668ABD; border-bottom-width:1px;}
        QPushButton#primaryDownload:disabled {background:#CED7E5; border-color:#CED7E5; color:#F6F8FC;}
        QPushButton#subtleBack {background:transparent; border:0; border-radius:10px; padding:12px 20px;}
        QPushButton#subtleBack:hover {background:#E4EBF5;}
"""

BACKGROUNDS_STYLE = """
/* 页面标题与顶部文本 */
QLabel#galleryTitle {
    font-size: 26px;
    font-weight: 600;
    color: #7799CC;
    padding: 6px 4px;
}
QLabel#downloadHeading {
    font-size: 26px;
    font-weight: 600;
    color: #384260;
}
QLabel#downloadSubHeading {
    font-size: 13px;
    color: #8C96AC;
}

/* 顶部搜索框：日系微光圆角胶囊 */
QLineEdit {
    background: rgba(255, 255, 255, 0.90);
    border: 1.5px solid #DFE5F0;
    border-radius: 17px;
    padding: 6px 18px;
    min-height: 22px;
    font-size: 13px;
    color: #384260;
}
QLineEdit:hover, QLineEdit:focus {
    background: #FFFFFF;
    border: 1.5px solid #7799CC;
}

/* 刷新/操作胶囊按钮 */
QPushButton#actionButton, QPushButton#refreshButton {
    background: rgba(255, 255, 255, 0.85);
    border: 1px solid #DFE5F0;
    border-radius: 14px;
    padding: 6px 16px;
    font-size: 12.5px;
    font-weight: 600;
    color: #616F8A;
}
QPushButton#actionButton:hover, QPushButton#refreshButton:hover {
    background: #FFFFFF;
    border-color: #7799CC;
    color: #4A6E9E;
}
QPushButton#actionButton:checked {
    background: #EAF0FA;
    border-color: #7799CC;
    color: #4A6E9E;
}

/* 复选框：显示图片名称 */
QCheckBox {
    font-size: 12.5px;
    color: #6A7790;
    spacing: 6px;
}
QCheckBox::indicator {
    width: 16px;
    height: 16px;
    border-radius: 4px;
    border: 1.5px solid #CCD5E4;
    background: rgba(255, 255, 255, 0.9);
}
QCheckBox::indicator:hover {
    border-color: #7799CC;
}
QCheckBox::indicator:checked {
    background: #7799CC;
    border-color: #7799CC;
    image: url("launcher/assets/check_white.png"); /* 若无图标会自动变实心蓝方块 */
}

/* 统计与标签文字 */
QLabel#pageCountNotice, QLabel#categoryTag {
    font-size: 12px;
    font-weight: 500;
    color: #838EA5;
}

/* 画廊主面板：通透单层圆角卡片，消除套娃感 */
QFrame#backgroundGalleryPanel {
    background: rgba(255, 255, 255, 0.65);
    border: 1px solid rgba(255, 255, 255, 0.85);
    border-radius: 16px;
    padding: 12px;
}
QScrollArea, QFrame#galleryContainer {
    background: transparent;
    border: none;
}
QListWidget#galleryContainer {
    background: transparent;
    border: none;
    padding: 0;
    outline: 0;
}
QListWidget#galleryContainer::item {
    background: transparent;
    border: 0;
    margin: 0;
}

/* 单个背景卡片项 (Item Card) */
QFrame#resourceCard {
    background: #FFFFFF;
    border: 1px solid #E2E8F4;
    border-radius: 12px;
    padding: 6px;
}
QFrame#resourceCard:hover {
    border-color: #7799CC;
    background: #FDFEFF;
}
QFrame#resourceCard QLabel {
    background: transparent;
}

/* 图片缩略图预览：紧凑圆角贴合 */
QFrame#resourceCard[compactCard="true"] {padding:0; border:0;}
QFrame#cardBottomBar {background:#FFFFFF; border:0; border-bottom-left-radius:12px; border-bottom-right-radius:12px;}
QFrame#cardBottomBar QLabel#resourceName {padding:0; font-size:11px;}
QFrame#cardBottomBar QPushButton#downloadItemBtn {padding:0 8px; min-height:20px; min-width:40px; max-width:108px; border-radius:8px;}
QLabel#resourcePreview[edgePreview="true"] {padding:0; border:0; border-top-left-radius:12px; border-top-right-radius:12px; border-bottom-left-radius:0; border-bottom-right-radius:0;}
QLabel#resourcePreview {
    background: #E8EEF6;
    border-radius: 8px;
}

/* 资源文件名与描述 */
QLabel#resourceName {
    font-size: 11.5px;
    font-weight: 600;
    color: #495675;
    padding-top: 4px;
}
QLabel#resourceDetail {
    font-size: 11px;
    color: #8C96AC;
}

/* 卡片内部的下载按钮：精巧药丸胶囊（不再横跨整张卡片） */
QPushButton#downloadItemBtn {
    background: #F1F4FA;
    border: 1px solid #DFE5F0;
    border-radius: 11px;
    color: #55627D;
    font-size: 12px;
    font-weight: 600;
    padding: 4px 16px;
    min-height: 22px;
    min-width: 64px;
    max-width: 108px;
}
QPushButton#downloadItemBtn:hover {
    background: #7799CC;
    border-color: #7799CC;
    color: #FFFFFF;
}
QPushButton#downloadItemBtn:pressed {
    background: #5E80B4;
}
QPushButton#downloadItemBtn:disabled {
    background: #F6F8FC;
    border-color: #EBF0F7;
    color: #B2BAC9;
}

/* 进度条 */
QProgressBar {
    border: 0;
    background: #E8EEF7;
    border-radius: 3px;
    height: 5px;
    text-align: center;
}
QProgressBar::chunk {
    background: #7799CC;
    border-radius: 3px;
}

/* 优雅纤细的垂直滚动条 */
QScrollBar:vertical {
    background: transparent;
    width: 8px;
    min-width: 8px;
    max-width: 8px;
    border: 0;
    margin: 4px 2px;
}
QScrollBar::handle:vertical {
    background: #CCD6E5;
    min-height: 36px;
    border-radius: 4px;
}
QScrollBar::handle:vertical:hover {
    background: #7799CC;
}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
    height: 0;
    border: 0;
    background: transparent;
}
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {
    background: transparent;
}

/* 底部分页控制器 */
QPushButton#pageBtn {
    background: rgba(255, 255, 255, 0.85);
    border: 1px solid #DFE5F0;
    border-radius: 12px;
    color: #5A6782;
    font-size: 12px;
    font-weight: 600;
    padding: 5px 16px;
}
QPushButton#pageBtn:hover {
    background: #FFFFFF;
    border-color: #7799CC;
    color: #4A6E9E;
}
QPushButton#pageBtn:disabled {
    background: transparent;
    border-color: transparent;
    color: #B5BED0;
}
QLabel#pageIndicator {
    font-size: 12.5px;
    font-weight: 600;
    color: #637089;
    padding: 0 10px;
}
"""

# 两类图片资源共用网格外观，字体与间距统一经过 ScreenMetrics 缩放。
AVATARS_STYLE = BACKGROUNDS_STYLE

MODELS_STYLE = """
QPushButton#refreshButton {font-size:13px; padding:4px 12px; border-radius:8px;}
QLabel#resourceName, QLabel#resourceDetail {padding:0;}
QListWidget::item {border-bottom:1px solid #E5E5E5;}
        QProgressBar {border:0; background:#CAD4E4; border-radius:3px; height:6px;}
        QProgressBar::chunk {background:#7799CC; border-radius:3px;}
        QLabel#downloadHeading {font-size:26px; font-weight:600;}
        QLabel#resourceDetail {font-size:14px; color:#8798B2;}
        QLabel#resourcePreview {background:#E5E5E5; border-radius:8px; color:#7799CC;}

QLabel#resourceName {font-weight:600;}
"""

PAGE_STYLES = {
    "common": COMMON_STYLE,
    "home": HOME_STYLE,
    "characters": CHARACTERS_STYLE,
    "backgrounds": BACKGROUNDS_STYLE,
    "avatars": AVATARS_STYLE,
    "models": MODELS_STYLE,
}

@dataclass
class ScreenMetrics:
    width: int
    height: int

    def px(self, reference):
        """把 1080 高度设计稿上的尺寸按当前屏幕可用高度换算。"""
        return max(1, round(self.height * reference / 1080))

    @staticmethod
    def window_size(screen):
        """按用户 2560×1440 屏幕上 1560×1200 的比例计算逻辑尺寸，避免重复乘 DPI。"""
        full = screen.geometry()
        available = screen.availableGeometry()
        # 留出系统标题栏与边框空间，异常窄小的工作区也不越界。
        return (min(round(full.width()*1560/2560), round(available.width()*.96)),
                min(round(full.height()*1200/1440), round(available.height()*.94)))

    def stylesheet(self):
        """沿用旧对话框颜色、按钮状态及边框，仅按屏幕比例换算尺寸。"""
        css = dialogWindowDefaultCss + self.read_style("common")
        for index in range(len(BAND_COLORS)):
            accent = self.band_accent(index)
            main = f'QPushButton[topTab="true"][bandIndex="{index}"]'
            sub = f'QPushButton[subMode="true"][bandIndex="{index}"]'
            css += f'{main}:checked {{background:white; color:{accent.name()};}}'
            css += f'{main}:hover, {sub}:hover {{color:{accent.name()};}}'
            css += f'{sub}:checked {{background:{accent.name()}; color:white;}}'
            css += f'{main}:disabled, {sub}:disabled {{color:#A9B3C4;}}'
        return self.scale_css(css)

    @staticmethod
    def band_accent(index):
        """统一顶部导航和底部主按钮的乐队强调色，压低亮度保证白字可读。"""
        accent = QColor(BAND_COLORS[index])
        while .299*accent.redF()+.587*accent.greenF()+.114*accent.blueF() > .43:
            accent = accent.darker(110)
        return accent

    def read_style(self, name):
        """取得集中维护的指定页面样式定义。"""
        return PAGE_STYLES[name]

    def scale_css(self, css):
        """统一换算公共与页面样式中的像素，保证各页面仍随屏幕缩放。"""
        return re.sub(r"(\d+(?:\.\d+)?)px", lambda m: f"{self.px(float(m[1]))}px", css)

    def page_stylesheet(self, page):
        """生成单页样式，角色页附加乐队状态色，其他页面互不污染。"""
        additions = self.read_style(page)
        if page != "characters":
            additions += """
QScrollBar:vertical {background:#EEF2F8; width:12px; min-width:12px; max-width:12px; margin:0; border:0;}
QScrollBar::handle:vertical {background:#AABAD1; min-height:36px; border-radius:4px;}
QScrollBar::handle:vertical:hover {background:#7799CC;}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {height:0; border:0; background:transparent;}
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {background:transparent;}
"""
            # 与模型下载页一致，继承应用字体，不在资源页另行指定字体族。
            return self.scale_css(additions)

        # 乐队识别色仅用于按钮边框，不影响角色主题色或数据模型。
        for index, color in enumerate(BAND_COLORS):
            additions += f'QToolButton#bandTile[bandIndex="{index}"]:checked {{border-color:{color};}}'
            # 压低按钮底色亮度，让黄色等浅色队伍也能使用清晰的白色文字。
            accent = self.band_accent(index)
            selector = f'QPushButton#primaryDownload[bandIndex="{index}"]'
            arrow = f'QToolButton#carouselArrow[bandIndex="{index}"]'
            additions += f'{arrow} {{color:{accent.name()}; border-color:{accent.name()};}}'
            additions += f'{arrow}:hover {{background:white; color:{accent.darker(110).name()}; border-color:{accent.name()};}}'
            additions += f'{selector} {{background:{accent.name()}; border-color:{accent.name()}; border-bottom-color:{accent.darker(118).name()};}}'
            additions += f'{selector}:hover {{background:{accent.lighter(110).name()};}}'
            additions += f'{selector}:pressed {{background:{accent.darker(110).name()};}}'
            additions += f'{selector}:disabled {{background:#CED7E5; border-color:#CED7E5; color:#F6F8FC;}}'
            receiver = f'QComboBox#characterReceiver[bandIndex="{index}"]'
            additions += f'{receiver} {{background:white; color:{accent.name()}; border-color:{accent.name()};}}'
            additions += f'{receiver}:hover, {receiver}:focus {{background:white; border-color:{accent.darker(118).name()};}}'
            additions += f'{receiver} QAbstractItemView {{background:white; color:{accent.name()}; selection-background-color:{accent.name()}; selection-color:white; border-color:{accent.name()};}}'
            back = f'QPushButton#subtleBack[bandIndex="{index}"]'
            additions += f'{back} {{color:{accent.name()};}}'
            additions += f'{back}:hover {{background:rgba({accent.red()},{accent.green()},{accent.blue()},20);}}'
        return self.scale_css(additions)
