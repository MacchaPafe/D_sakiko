from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Optional, Sequence
import math
import uuid
import re

from PyQt5.QtCore import Qt, QSize, QRectF, QTimer, QVariantAnimation, QEasingCurve, pyqtSignal, QUrl, QPointF
from PyQt5.QtGui import QColor, QPainter, QPixmap, QIcon, QDesktopServices, QFont, QPainterPath, QFontMetricsF, QLinearGradient, QRadialGradient, QPen, QCursor, QPaintEvent, QMouseEvent
from PyQt5.QtWidgets import (QDialog, QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QPushButton, QStackedWidget, QComboBox, QScrollArea, QButtonGroup, QToolButton, QMessageBox,
    QListWidget, QListWidgetItem, QGroupBox, QProgressBar, QLineEdit, QApplication, QLayout, QGridLayout, QFrame, QGraphicsDropShadowEffect, QGraphicsOpacityEffect, QSizePolicy, QDialogButtonBox)

from .catalog import APP_ROOT, PROJECT_ROOT, CACHE_ROOT, BANDS, CHARACTERS, CHARACTER_ROLES, Selection, destination
from .service import ResourceService, Resource
from .legacy import LegacyService
from .installer import V3Installer, InstallResult
from .installed_models import find_installed_v3
from .jobs import JobHub
from .appearance import ScreenMetrics, BAND_COLORS
from .targets import LocalTarget, read_targets, creation_conflicts, default_target




class CharacterComboBox(QComboBox):
    def find_target(self, identifier: str) -> int:
        """按目录标识查找接收角色，避免 Qt 对 Python 元组数据的比较差异。"""
        return next((index for index in range(1, self.count())
                     if self.itemData(index)[0] == identifier), -1)

    def paintEvent(self, event):
        """绘制统一配色的下拉箭头，替代 Windows 原生方形按钮。"""
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        size = self.height() * .12
        x, y = self.width()-self.height()*.48, self.height()*.47
        path = QPainterPath()
        path.moveTo(x-size, y-size*.4)
        path.lineTo(x, y+size*.6)
        path.lineTo(x+size, y-size*.4)
        painter.setPen(self.palette().text().color())
        painter.drawPath(path)


class CharacterCarousel(QWidget):
    selected = pyqtSignal(str)
    requested = pyqtSignal(str)

    def __init__(self) -> None:
        """初始化角色轮播状态与立绘淡入动画。"""
        super().__init__()
        self.members, self.index = (), 0
        self.pictures, self.failures = {}, set()
        self.portrait_shadows = {}
        self._card_images: dict[str, tuple[tuple[float, float, float, int, int, float, str, bool], QPixmap]] = {}
        self.start_x: Optional[int] = None
        self._drag_start_position = 0.0
        self._drag_step = 1.0
        self._dragged = False
        self._position = 0.0
        self.slide_target = 0
        self.slide = QVariantAnimation(self)
        self.slide.setDuration(320)
        self.slide.setEasingCurve(QEasingCurve.OutCubic)
        self.slide.valueChanged.connect(self._slide)
        self.slide.finished.connect(self._settled)
        self.alphas, self.fades = {}, {}
        self.visible_characters = set()
        self.setMinimumHeight(round(QApplication.primaryScreen().availableGeometry().height() * .3))
        self.setCursor(Qt.OpenHandCursor)

        self.arrows = []
        self.arrow_layers, self.arrow_fades, self.arrow_targets = [], [], [False,False]
        for text, direction in (("‹", -1), ("›", 1)):
            layer = QWidget(self)
            layer.setProperty("themeSurface", True)
            layer.setAttribute(Qt.WA_TransparentForMouseEvents,True)
            opacity = QGraphicsOpacityEffect(layer)
            opacity.setOpacity(0.0)
            layer.setGraphicsEffect(opacity)
            animation = QVariantAnimation(self)
            animation.setDuration(180)
            animation.setEasingCurve(QEasingCurve.InOutCubic)
            animation.valueChanged.connect(opacity.setOpacity)
            self.arrow_layers.append(layer)
            self.arrow_fades.append(animation)
            button = QToolButton(layer)
            button.setObjectName("carouselArrow")
            button.setText(text)
            button.setToolTip("上一位" if direction < 0 else "下一位")
            shadow = QGraphicsDropShadowEffect(button)
            shadow.setColor(QColor(35,45,70,38))
            button.setGraphicsEffect(shadow)
            button.clicked.connect(lambda checked, n=direction: self.shift(n))
            self.arrows.append(button)
        self.arrow_hover_timer = QTimer(self)
        self.arrow_hover_timer.setInterval(40)
        self.arrow_hover_timer.timeout.connect(self.update_arrow_hover)

    def update_arrow_hover(self, point=None):
        """检测左右各 100 设计像素的边缘热区，子按钮上悬停也不会反复淡出。"""
        if point is None:
            point = self.mapFromGlobal(QCursor.pos())
        metrics = getattr(self.window(),"metrics",None)
        width = metrics.px(100) if metrics else round(QApplication.primaryScreen().availableGeometry().height()*100/1080)
        width = min(width,self.width()/2)
        inside = self.isVisible() and self.rect().contains(point)
        for index,active in enumerate((inside and point.x()<width,inside and point.x()>=self.width()-width)):
            if active == self.arrow_targets[index]:
                continue
            self.arrow_targets[index] = active
            layer,animation = self.arrow_layers[index],self.arrow_fades[index]
            layer.setAttribute(Qt.WA_TransparentForMouseEvents,not active)
            animation.stop()
            animation.setStartValue(float(layer.graphicsEffect().opacity()))
            animation.setEndValue(1.0 if active else 0.0)
            animation.start()

    def showEvent(self, event):
        """角色页显示时启用悬停检测。"""
        super().showEvent(event)
        self.arrow_hover_timer.start()
        self.update_arrow_hover()

    def hideEvent(self, event):
        """离开角色页时停止检测，并清理箭头的可见和点击状态。"""
        self.arrow_hover_timer.stop()
        for index,layer in enumerate(self.arrow_layers):
            self.arrow_fades[index].stop()
            self.arrow_targets[index] = False
            layer.graphicsEffect().setOpacity(0.0)
            layer.setAttribute(Qt.WA_TransparentForMouseEvents,True)
        super().hideEvent(event)

    def _fade(self, character, value):
        """独立更新每个人物的透明度，让侧卡和中心卡同时淡入。"""
        if value is None:
            return
        self.alphas[character] = max(0.0,min(1.0,float(value)))
        self.update()

    def fade_picture(self, character):
        """重新展示立绘时播放淡入，包括已经解码到内存中的缓存图片。"""
        animation = self.fades.get(character)
        if animation is None:
            animation = QVariantAnimation(self)
            animation.setDuration(280)
            animation.valueChanged.connect(lambda value,c=character:self._fade(c,value))
            self.fades[character] = animation
        animation.stop()
        self.alphas[character] = 0.0
        animation.setStartValue(0.0)
        animation.setEndValue(1.0)
        animation.start()

    def set_members(self, members: Sequence[str]) -> None:
        """切换分组成员并回到该组的第一位角色。"""
        self.slide.stop()
        self.slide_target = 0
        self._position = 0.0
        self.start_x = None
        self._dragged = False
        self.setCursor(Qt.OpenHandCursor)
        self.members, self.index = members, 0
        self._card_images.clear()
        self.visible_characters.clear()
        self.changed()

    def resizeEvent(self, event):
        """让左右箭头固定在卡片区域两侧，并按内容区高度缩放。"""
        super().resizeEvent(event)
        size = max(1, round(self.height()*.075))
        for index, button in enumerate(self.arrows):
            padding = max(2,round(size*.3))
            layer = self.arrow_layers[index]
            layer.setGeometry((round(self.width()*.012) if index == 0 else round(self.width()*.988)-size)-padding,
                              (self.height()-size)//2-padding,size+2*padding,size+2*padding)
            button.setGeometry(padding,padding,size,size)
            layer.raise_()
            button.graphicsEffect().setBlurRadius(max(1,size*.24))
            button.graphicsEffect().setOffset(0,max(1,size*.045))

    def changed(self) -> None:
        """正式提交选择，再准备当前展示与补位卡片。"""
        if self.members:
            self.selected.emit(self.members[self.index])
        self._prepare_cards(fade_cached=not self.visible_characters)
        self.update()

    def _prepare_cards(self, fade_cached: bool = False) -> None:
        """随绘制窗口预取立绘并回收海报缓存，拖动途中不提交角色选择。"""
        if not self.members:
            self.visible_characters.clear()
            self._card_images.clear()
            return
        offsets = self.card_offsets()
        characters = dict.fromkeys(self.members[(self.index+delta)%len(self.members)]
                                   for delta in sorted(offsets, key=lambda d: abs(d + self._position)))
        # 仅整组首次展示淡入；缓存补位始终沿用已有立绘，避免落位后重新闪现。
        if fade_cached:
            for character in characters:
                if character in self.pictures:
                    self.fade_picture(character)
        self.visible_characters = set(characters)
        self._card_images = {character: cached for character, cached in self._card_images.items()
                             if character in self.visible_characters}
        for character in characters:
            if character not in self.pictures:
                self.requested.emit(character)

    def shift(self, amount: int) -> None:
        """按钮及侧卡点击仍只切换一位，拖动和动画期间不重复启动。"""
        if self.start_x is not None or self.slide.state() == QVariantAnimation.Running:
            return
        self._animate_to((1 if amount > 0 else -1) if amount else 0)

    def _animate_to(self, target: int) -> None:
        """吸附到指定逻辑槽位，剩余距离越短动画越短，已到位时直接提交。"""
        if len(self.members) < 2:
            self._position = 0.0
            self.update()
            return
        self.slide_target = target
        distance = abs(self._position + target)
        if distance * self.card_step() < .5:
            self._settled()
            return
        # 鼠标坐标为 int，Qt 不会自动为 int/float 混合端点选择插值器。
        # 同时替换端点时屏蔽旧端点参与产生的瞬时 valueChanged。
        self.slide.blockSignals(True)
        self.slide.setDuration(max(1, round(320 * distance)))
        self.slide.setStartValue(float(self._position))
        self.slide.setEndValue(float(-self.slide_target))
        self.slide.blockSignals(False)
        self.slide.start()

    @property
    def drag_offset(self) -> float:
        """将逻辑槽位进度换算为像素，窗口缩放时保持动画进度不变。"""
        return self._position * self.card_step()

    @drag_offset.setter
    def drag_offset(self, value: float) -> None:
        """把鼠标像素位移转换成统一的槽位进度。"""
        self._slide(float(value) / max(1.0, self.card_step()))

    def card_step(self) -> float:
        """以中心到近侧槽位的距离作为一次拖动的长度。"""
        width = min(self.height() * .96 * .72, self.width() * .52)
        return max(1.0, self._slot_state(1.0)[0] * width)

    def _slide(self, value: Optional[float]) -> None:
        """同步无界槽位进度，越过槽位边界时滚动有限的卡片绘制窗口。"""
        if value is None:
            return
        previous_anchor = math.floor(-self._position)
        self._position = float(value)
        if math.floor(-self._position) != previous_anchor:
            self._prepare_cards()
        self.update()

    def _settled(self) -> None:
        """吸附结束后提交角色选择，并无缝重置绘制坐标。"""
        if self.members:
            self.index = (self.index + self.slide_target) % len(self.members)
        self._position = 0.0
        self.slide_target = 0
        self.changed()

    def set_picture(self, character, data):
        """在主线程解码并缩放立绘，当前角色的新图片淡入显示。"""
        pixmap = QPixmap()
        if data and pixmap.loadFromData(data):
            self.pictures[character] = pixmap
            self.portrait_shadows[character] = self.make_portrait_shadow(pixmap)
            self.failures.discard(character)
            if character in self.visible_characters:
                self.fade_picture(character)
        else:
            self.failures.add(character)
        self.update()

    def make_portrait_shadow(self, pixmap):
        """按透明轮廓生成低分辨率柔化投影并缓存，避免每帧处理原始立绘。"""
        silhouette = pixmap.scaledToHeight(720, Qt.SmoothTransformation)
        painter = QPainter(silhouette)
        painter.setCompositionMode(QPainter.CompositionMode_SourceIn)
        painter.fillRect(silhouette.rect(), QColor(26, 35, 64, 100))
        painter.end()
        small = silhouette.scaledToHeight(120, Qt.SmoothTransformation)
        return small.scaled(silhouette.size(), Qt.IgnoreAspectRatio, Qt.SmoothTransformation)

    def card_offsets(self) -> tuple[int, ...]:
        """围绕当前进度维护展示槽位与两端补位，长距离拖动也只绘制有限卡片。"""
        count = min(5, len(self.members))
        if count < 2:
            return (0,) if count else ()
        left, right = (count - 1) // 2, count // 2
        anchor = math.floor(-self._position)
        return tuple(range(anchor - left - 1, anchor + right + 2))

    def _slot_state(self, position: float) -> tuple[float, float, float, float]:
        """插值横向位置、缩放、下沉及透明度，端点收进前方圆角卡片内。"""
        width = max(1.0, min(self.height() * .96 * .72, self.width() * .52))
        outer_edge = min(1.22, self.width() / (2 * width) - .025)
        near_x = .5 + (outer_edge - .5) * .8 - .83 / 2
        far_x = outer_edge - .66 / 2
        states = [(0.0, 1.0, 0.0, 1.0), (near_x, .83, .025, .6),
                  (far_x, .66, .05, .3)]
        count = min(5, len(self.members))
        extent = (count - 1) // 2 if position < 0 else count // 2
        extent = max(0, extent)
        states = states[:extent + 1]
        x, scale, down, opacity = states[-1]
        states.append((x + scale * .04, scale * .9, down, opacity))
        distance = min(abs(position), float(len(states) - 1))
        start = min(int(distance), len(states) - 2)
        fraction = distance - start
        first, second = states[start], states[start + 1]
        values = tuple(a + (b - a) * fraction for a, b in zip(first, second))
        return (-values[0] if position < 0 else values[0], values[1], values[2], values[3])

    def card_rect(self, delta: int) -> QRectF:
        """沿收紧的槽位轨迹移动卡片，外侧只露窄边，隐藏端点完全内含。"""
        height = self.height() * .96
        width = min(height * .72, self.width() * .52)
        x, scale, down, _ = self._slot_state(delta + self._position)
        return QRectF(self.width()/2 + width*x - width*scale/2,
                      (self.height()-height*scale)/2 + height*down, width*scale, height*scale)

    def card_layers(self, front_delta: Optional[int] = None) -> list[tuple[int, QPainterPath]]:
        """按前后关系裁掉被覆盖区域，避免半透明侧卡透出隐藏补位。"""
        covered = QPainterPath()
        viewport = QPainterPath()
        viewport.addRect(QRectF(self.rect()))
        layers: list[tuple[int, QPainterPath]] = []
        order = sorted(self.card_offsets(), key=lambda d: (d != front_delta, abs(d + self._position), d + self._position))
        for delta in order:
            rect = self.card_rect(delta)
            outline = QPainterPath()
            outline.addRoundedRect(rect, rect.width()*.045, rect.width()*.045)
            clip = viewport.subtracted(covered)
            if not outline.intersected(clip).isEmpty():
                layers.append((delta, clip))
            covered = covered.united(outline)
        return list(reversed(layers))

    def draw_text(self, painter, text, rect, size, color, bold=False):
        """按卡片尺寸设置文字，并在长姓名超宽时缩小到可用宽度。"""
        font = QFont(self.font())
        font.setBold(bold)
        if bold:
            font.setLetterSpacing(QFont.AbsoluteSpacing, size*.035)
        font.setPixelSize(max(1, round(size)))
        metrics = QFontMetricsF(font)
        if metrics.horizontalAdvance(text) > rect.width():
            font.setPixelSize(max(1, int(size * rect.width()/metrics.horizontalAdvance(text))))
        painter.setFont(font)
        painter.setPen(color)
        painter.drawText(rect, Qt.AlignLeft | Qt.AlignVCenter, text)

    def paintEvent(self, event: QPaintEvent) -> None:
        """绘制轮播；中心两卡交接时短暂混合前后层次，避免遮挡突变。"""
        if not self.members:
            return
        canvas = QPainter(self)
        left = math.floor(-self._position)
        fraction = -self._position - left
        blend = max(0.0, min(1.0, (fraction - .35) / .3))
        blend = blend * blend * (3 - 2 * blend)
        if blend in (0.0, 1.0):
            self._paint_cards(canvas, self.card_layers(left if blend == 0.0 else left + 1))
        else:
            ratio = self.devicePixelRatioF()
            composite = QPixmap(round(self.width()*ratio), round(self.height()*ratio))
            composite.setDevicePixelRatio(ratio)
            composite.fill(Qt.transparent)
            mixer = QPainter(composite)
            mixer.setCompositionMode(QPainter.CompositionMode_Plus)
            for front, weight in ((left, 1 - blend), (left + 1, blend)):
                scene = QPixmap(composite.size())
                scene.setDevicePixelRatio(ratio)
                scene.fill(Qt.transparent)
                scene_painter = QPainter(scene)
                self._paint_cards(scene_painter, self.card_layers(front))
                scene_painter.end()
                mixer.setOpacity(weight)
                mixer.drawPixmap(0, 0, scene)
            mixer.end()
            canvas.drawPixmap(0, 0, composite)
        canvas.end()

    def _paint_cards(self, canvas: QPainter, layers: list[tuple[int, QPainterPath]]) -> None:
        """按遮挡区域合成角色海报，整张卡片共同缩放和调整透明度。"""
        canvas.setRenderHint(QPainter.Antialiasing)
        canvas.setRenderHint(QPainter.SmoothPixmapTransform)
        for delta, clip in layers:
            canvas.save()
            canvas.setClipPath(clip)
            character = self.members[(self.index + delta) % len(self.members)]
            rect = self.card_rect(delta)
            w, h = rect.width(), rect.height()
            focus = max(0.0, 1.0-abs(delta+self._position))
            opacity = self._slot_state(delta + self._position)[3]
            radius = w*.045
            # 分层描绘柔和投影；所有距离随卡片大小缩放。
            canvas.setPen(Qt.NoPen)
            for spread in range(12, 0, -1):
                padding = w*.0025*spread
                canvas.setBrush(QColor(38, 48, 82, round((1+spread*.12)*focus)))
                canvas.drawRoundedRect(rect.adjusted(-padding, -padding, padding, padding).translated(0,h*.009),
                                       radius+padding, radius+padding)
            layer = self._card_image(character)
            canvas.setOpacity(opacity)
            ratio = self.devicePixelRatioF()
            source_height = self.height() * .96
            source_width = min(source_height * .72, self.width() * .52)
            canvas.drawPixmap(rect, layer, QRectF(0, 0, source_width*ratio, source_height*ratio))
            canvas.restore()

    def _card_image(self, character: str) -> QPixmap:
        """缓存中心尺寸的完整海报，运动帧只缩放合成，避免反复排字和绘制立绘。"""
        h = self.height() * .96
        w = min(h * .72, self.width() * .52)
        ratio = self.devicePixelRatioF()
        picture = self.pictures.get(character)
        shadow = self.portrait_shadows.get(character)
        key = (w, h, ratio, picture.cacheKey() if picture is not None else 0,
               shadow.cacheKey() if shadow is not None else 0, self.alphas.get(character, 1.0),
               self.font().toString(), character in self.failures)
        cached = self._card_images.get(character)
        if cached is not None and cached[0] == key:
            return cached[1]
        info = CHARACTERS[character]
        color = QColor(info["theme_color"])
        x = y = 0.0
        rect = QRectF(x, y, w, h)
        radius = w * .045
        # 先合成为整张卡片，再统一设置透明度，避免叠加图层使侧卡颜色变浓。
        layer = QPixmap(round(w*ratio)+2, round(h*ratio)+2)
        layer.setDevicePixelRatio(ratio)
        layer.fill(Qt.transparent)
        painter = QPainter(layer)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setRenderHint(QPainter.SmoothPixmapTransform)
        outline = QPainterPath()
        outline.addRoundedRect(rect, radius, radius)
        painter.setClipPath(outline)
        hue, saturation, value, _ = color.getHsvF()
        base = QColor.fromHsvF(max(0,hue), saturation*.67, min(.87,max(.58,value*.9)))
        gradient = QLinearGradient(x,y,x,y+h)
        gradient.setColorAt(0,base.lighter(118))
        gradient.setColorAt(1,base.darker(112))
        painter.fillRect(rect, gradient)
        painter.setPen(QPen(QColor(255,255,255,13), max(1,w*.002)))
        for stripe in range(-8, 16):
            sx = x+stripe*w*.15
            painter.drawLine(QPointF(sx,y),
                             QPointF(sx+h*.45,y+h))
        glow = QLinearGradient(x,y+h*.2,x+w,y+h*.7)
        glow.setColorAt(0,QColor(255,255,255,0))
        glow.setColorAt(.65,QColor(255,255,255,35))
        glow.setColorAt(1,QColor(255,255,255,0))
        painter.fillRect(rect,glow)
        if character in self.pictures:
            picture = self.pictures[character]
            image_h = h * 1.5
            image_w = image_h * picture.width()/picture.height()
            portrait = QRectF(x+w*.63-image_w/2, y+h*.15, image_w, image_h)
            painter.setOpacity(self.alphas.get(character,1.0))
            shadow = self.portrait_shadows.get(character)
            if shadow is not None:
                painter.drawPixmap(portrait.translated(w*.018,h*.009),shadow,QRectF(shadow.rect()))
            painter.drawPixmap(portrait, picture, QRectF(picture.rect()))
            painter.setOpacity(1)
        elif character in self.failures:
            self.draw_text(painter, "立绘暂不可用", QRectF(x+w*.1,y+h*.4,w*.8,h*.08),w*.047,QColor("#5F6368"))
        # 底部色雾覆盖立绘末端，让裁切自然融入卡片底色。
        mist = QLinearGradient(x,y+h*.65,x,y+h)
        mist.setColorAt(0,QColor(base.red(),base.green(),base.blue(),0))
        mist.setColorAt(1,QColor(base.red(),base.green(),base.blue(),175))
        painter.fillRect(rect,mist)
        full = info["full_name"]
        chinese = full.split("（", 1)[0]
        match = re.search(r"（([^）]+)）", full)
        japanese = match[1].split("-", 1)[0] if match else info["display_name"]
        # 深色角色底色使用白字；亮色保持草图中的深灰标题。
        luminance = .2126*color.redF()+.7152*color.greenF()+.0722*color.blueF()
        ink = QColor("#5F6368") if luminance > .55 else QColor("#FFFFFF")
        self.draw_text(painter,chinese,QRectF(x+w*.06,y+h*.025,w*.88,h*.10),w*.115,ink,True)
        # 副标题使用深墨色；仅深色背景改用淡蓝白，避免亮底白字消失。
        top = base.lighter(118)
        lightness = .2126*top.redF()+.7152*top.greenF()+.0722*top.blueF()
        subtitle_ink = QColor("#35425B") if lightness > .48 else QColor("#E2EAF5")
        self.draw_text(painter,japanese,QRectF(x+w*.065,y+h*.125,w*.83,h*.065),w*.050,subtitle_ink)
        band = next((title for identifier, title, members in BANDS if identifier != "others" and character in members), "")
        if band:
            self.draw_badge(painter, band, x+w*.065,y+h*.825,w*.86,h*.055,w*.048)
        role = CHARACTER_ROLES.get(character, "")
        if role:
            self.draw_badge(painter,role,x+w*.065,y+h*.902,w*.75,h*.049,w*.043)
        painter.end()
        self._card_images[character] = (key, layer)
        return layer

    def draw_badge(self, painter, text, x, y, max_width, height, size):
        """用半透明深色胶囊承载乐队名或声部，长名称自动缩小。"""
        font = QFont(self.font())
        font.setPixelSize(max(1,round(size)))
        font.setBold(True)
        padding = height*.45
        width = min(max_width,QFontMetricsF(font).horizontalAdvance(text)+padding*2)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(25,34,58,135))
        painter.drawRoundedRect(QRectF(x,y,width,height),height*.45,height*.45)
        self.draw_text(painter,text,QRectF(x+padding,y,width-padding*2,height),size,QColor("white"),True)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        """记录拖动起点与固定换算比例，整次手势不因卡片缩放而改变速率。"""
        if event.button() == Qt.LeftButton and len(self.members) > 1 and self.slide.state() != QVariantAnimation.Running:
            self.start_x = event.x()
            self._drag_start_position = self._position
            self._drag_step = self.card_step()
            self._dragged = False
            self.setCursor(Qt.ClosedHandCursor)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        """按固定比例跟随鼠标，允许跨过任意多个角色或完整循环。"""
        if self.start_x is not None and len(self.members) > 1:
            distance = event.x() - self.start_x
            self._dragged = self._dragged or abs(distance) >= QApplication.startDragDistance()
            if self._dragged:
                self._slide(self._drag_start_position + distance / self._drag_step)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        """拖动松手吸附到最近角色；未发生拖动时保留侧卡单步点击。"""
        if event.button() != Qt.LeftButton or self.start_x is None:
            return
        self.mouseMoveEvent(event)
        self.start_x = None
        self.setCursor(Qt.OpenHandCursor)
        if self._dragged:
            self._dragged = False
            self._animate_to(math.floor(-self._position + .5))
            return
        center_width = min(self.height() * .96 * .72, self.width() * .52)
        center = QRectF(self.width()/2-center_width/2,0,center_width,self.height())
        if event.x() < center.left():
            self.shift(-1)
        elif event.x() > center.right():
            self.shift(1)
        else:
            self.shift(0)
        self.update()


class CharacterPage(QWidget):
    def __init__(self, window):
        """构建本地接收角色、乐队按钮和来源角色卡片选择页。"""
        super().__init__()
        self.window = window
        self.pending = set()
        layout = QVBoxLayout(self)
        self.receiver = CharacterComboBox()
        self.receiver.setObjectName("characterReceiver")
        self.receiver.addItem("需要先选择包内已有角色", None)
        for character in window.character_list:
            self.receiver.addItem(character.character_name,
                                  (character.character_folder_name, character.character_name))
        self.receiver.currentIndexChanged.connect(self.receiver_changed)
        layout.addWidget(self.receiver)
        self.source_box = QWidget()
        body = QVBoxLayout(self.source_box)
        band_bar = QWidget()
        band_layout = QGridLayout(band_bar)
        for column in range(7):
            band_layout.setColumnStretch(column,1)
        group = QButtonGroup(self)
        self.band_buttons = []
        for index, (identifier, title, _) in enumerate(BANDS):
            button = QToolButton()
            button.setObjectName("bandTile")
            button.setProperty("bandIndex", str(index))
            button.setText(title)
            button.setToolTip(title)
            if identifier == "others":
                button.setObjectName("otherCharacters")
                button.setText("其他角色")
                button.setToolTip("其他角色")
            button.setCheckable(True)
            button.setMinimumWidth(0)
            button.setFixedHeight(window.metrics.px(65))
            button.setSizePolicy(button.sizePolicy().Expanding, button.sizePolicy().Fixed)
            if identifier != "others":
                button.setIcon(QIcon(str(APP_ROOT / "assets/band_logo" / (identifier + ".png"))))
                button.setIconSize(QSize(window.metrics.px(120), window.metrics.px(54)))
                button.setToolButtonStyle(Qt.ToolButtonIconOnly)
            button.clicked.connect(lambda checked, i=index: self.select_band(i))
            group.addButton(button)
            # 第二行首格留空，五队占中间五格，其他角色位于最后一格。
            band_layout.addWidget(button, index//7, index if index < 7 else index-6)
            self.band_buttons.append(button)
        body.addWidget(band_bar)
        self.carousel = CharacterCarousel()
        self.carousel.selected.connect(self.select_character)
        self.carousel.requested.connect(self.request_portrait)
        body.addWidget(self.carousel, 1)
        layout.addWidget(self.source_box, 1)
        nav = QHBoxLayout()
        self.footer_logo = QLabel(self)
        self.footer_logo.setObjectName("footerBandLogo")
        self.footer_logo.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
        self.footer_identifier = None
        self.footer_target = None
        self.footer_effect = QGraphicsOpacityEffect(self.footer_logo)
        self.footer_effect.setOpacity(1.0)
        self.footer_logo.setGraphicsEffect(self.footer_effect)
        self.footer_animation = QVariantAnimation(self)
        self.footer_animation.setEasingCurve(QEasingCurve.InOutCubic)
        self.footer_animation.valueChanged.connect(self.footer_effect.setOpacity)
        self.footer_animation.finished.connect(self.finish_footer_transition)
        self.footer_fading_out = False
        nav.addWidget(self.footer_logo,0,Qt.AlignLeft)
        self.target_hint = QLabel("",self)
        self.target_hint.setObjectName("modeHint")
        self.next = QPushButton("查看可下载资源")
        self.next.setObjectName("primaryDownload")
        self.next.clicked.connect(window.show_resources)
        self.carousel.slide.stateChanged.connect(self.update_next)
        nav.addWidget(self.target_hint, 0, Qt.AlignLeft)
        nav.addStretch(1)
        nav.addWidget(self.next, 0, Qt.AlignRight)
        layout.addLayout(nav)
        self.credit = QPushButton("素材与资源来自Bang Dream GBP & OurNotes",self)
        self.credit.setObjectName("resourceCredit")
        self.credit.setCursor(Qt.PointingHandCursor)
        self.credit.setToolTip("点击隐藏")
        policy = self.credit.sizePolicy()
        policy.setRetainSizeWhenHidden(True)
        self.credit.setSizePolicy(policy)
        self.credit.clicked.connect(self.credit.hide)
        layout.addWidget(self.credit,0,Qt.AlignHCenter)

    def update_footer_logo(self):
        """切换队标时从当前透明度淡出，屏幕缩放或重复选择不重播动画。"""
        index = next((i for i,button in enumerate(self.band_buttons) if button.isChecked()),0)
        identifier = BANDS[index][0]
        px = self.window.metrics.px
        self.footer_logo.setFixedSize(px(160),px(50))
        if self.footer_identifier is None:
            self.footer_identifier = self.footer_target = identifier
        self.render_footer_logo()
        if identifier == self.footer_target:
            return
        self.footer_target = identifier
        self.footer_animation.stop()
        self.footer_fading_out = True
        self.footer_animation.setDuration(140)
        self.footer_animation.setStartValue(self.footer_effect.opacity())
        self.footer_animation.setEndValue(0.0)
        self.footer_animation.start()

    def render_footer_logo(self):
        """按当前尺寸显示队标；其他角色留空，避免隐藏控件引起布局跳动。"""
        path = APP_ROOT / "assets/band_logo" / (self.footer_identifier + "_small.png")
        pixmap = QPixmap(str(path)) if path.is_file() else QPixmap()
        # 面积缩小 15%，宽高各乘 sqrt(0.85)，保留原容器以免底栏布局位移。
        scale = .85 ** .5
        size = QSize(round(self.footer_logo.width()*scale),round(self.footer_logo.height()*scale))
        self.footer_logo.setPixmap(pixmap.scaled(size,Qt.KeepAspectRatio,Qt.SmoothTransformation)
                                   if not pixmap.isNull() else QPixmap())

    def finish_footer_transition(self):
        """旧队标淡出后替换最新目标，再淡入，连续切换不使用过期目标。"""
        if not self.footer_fading_out:
            return
        self.footer_fading_out = False
        self.footer_identifier = self.footer_target
        self.render_footer_logo()
        self.footer_animation.setDuration(200)
        self.footer_animation.setStartValue(0.0)
        self.footer_animation.setEndValue(1.0)
        self.footer_animation.start()

    def reset(self):
        """进入新模式时清空接收角色并重置来源角色选择。"""
        self.receiver.blockSignals(True)
        self.receiver.setCurrentIndex(0)
        self.receiver.blockSignals(False)
        self.receiver.setVisible(self.window.selection.mode == "existing")
        self.source_box.setEnabled(True)
        self.select_band(0)
        self.update_next()

    def receiver_changed(self) -> None:
        """保存手动接收角色，原地更新服装状态，不自动重新匹配。"""
        value = self.receiver.currentData()
        if self.window.selection.mode != "existing":
            value = None
        self.window.selection = replace(self.window.selection, target_id=value[0] if value else "",
                                        target_name=value[1] if value else "")
        self.source_box.setEnabled(True)
        self.update_next()
        self.window.selection_notice.clear()
        self.window.selection_notice.hide()
        self.window.resources.update_target()

    def select_band(self, index):
        """选中乐队按钮并加载本地配置的成员顺序。"""
        self.band_buttons[index].setChecked(True)
        self.update_footer_logo()
        for button in (self.next,self.receiver,*self.carousel.arrows):
            button.setProperty("bandIndex",str(index))
            button.style().unpolish(button)
            button.style().polish(button)
            button.update()
        self.window.transition_background(index)
        self.window.set_navigation_band(index)
        self.carousel.set_members(BANDS[index][2])

    def select_character(self, character):
        """更新来源角色快照和说明，不改动本地接收角色。"""
        self.window.selection = replace(self.window.selection, source=character)
        self.update_next()

    def update_next(self) -> None:
        """允许先浏览模型，接收角色在开始下载前补全。"""
        choice = self.window.selection
        self.next.setText("查看可下载头像" if choice.mode == "avatar" else "查看可下载模型")
        self.target_hint.setText("" if choice.mode == "existing" and not choice.target_id else "")
        self.next.setEnabled(bool(choice.source) and self.carousel.slide.state() != QVariantAnimation.Running)

    def retry_portrait(self):
        """强制重新获取当前立绘，绕过旧图片缓存。"""
        if self.carousel.members:
            character = self.carousel.members[self.carousel.index]
            self.carousel.pictures.pop(character, None)
            self.request_portrait(character, refresh=True)

    def request_portrait(self, character, refresh=False):
        """异步读取角色立绘并去重，关闭后不再更新界面。"""
        if character in self.pending or self.window.closing:
            return
        self.pending.add(character)
        def done(data, error):
            """接收后台结果；先检查窗口或页面代次，再更新对应控件。"""
            self.pending.discard(character)
            if not self.window.closing:
                self.carousel.set_picture(character, data if not error else None)
        self.window.hub.submit(lambda event, progress: self.window.service.preview(
            f"images/portraits/{character}.png", event, refresh=refresh), done)


class ElidedResourceLabel(QLabel):
    def setText(self, text):
        """保存完整名称或状态，按当前宽度省略显示并提供悬停全文。"""
        self.full_text = text
        self.setToolTip(text)
        super().setText(self.fontMetrics().elidedText(text,Qt.ElideMiddle,max(1,self.width())))

    def resizeEvent(self, event):
        """操作条宽度变化后重新计算省略文本。"""
        super().resizeEvent(event)
        self.setText(getattr(self,"full_text",""))


class PreviewLabel(QLabel):
    clicked = pyqtSignal()

    def mouseReleaseEvent(self, event):
        """点击缩略图时请求打开原图预览。"""
        if event.button() == Qt.LeftButton and self.rect().contains(event.pos()):
            self.clicked.emit()
        super().mouseReleaseEvent(event)

    def paintEvent(self, event):
        """背景缩略图裁切顶部圆角，使图片贴边而不盖住卡片圆角。"""
        if not self.property("edgePreview") or self.pixmap() is None:
            return super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        radius = self.window().metrics.px(12)
        clip = QPainterPath()
        clip.addRoundedRect(QRectF(self.rect()),radius,radius)
        clip.addRect(QRectF(0,self.height()/2,self.width(),self.height()/2))
        clip.setFillRule(Qt.WindingFill)
        painter.setClipPath(clip)
        pixmap = self.pixmap()
        painter.drawPixmap((self.width()-pixmap.width())//2,(self.height()-pixmap.height())//2,pixmap)


class OriginalPreview(QDialog):
    def __init__(self, row):
        """创建可缩放的原图窗口，读取任务复用项目内预览缓存。"""
        super().__init__(row.page.window)
        self.row, self.closed, self.pixmap, self.job = row, False, None, None
        self.setAttribute(Qt.WA_DeleteOnClose)
        self.setWindowModality(Qt.WindowModal)
        self.setWindowTitle(row.title + " · 原图预览")
        self.resize(round(row.page.window.width()*.82),round(row.page.window.height()*.82))
        self.setStyleSheet(row.page.window.metrics.page_stylesheet("backgrounds"))
        layout = QVBoxLayout(self)
        self.notice = QLabel("正在加载原图…",self)
        layout.addWidget(self.notice)
        self.scroll = QScrollArea(self)
        self.scroll.setAlignment(Qt.AlignCenter)
        self.picture = QLabel(self.scroll)
        self.picture.setAlignment(Qt.AlignCenter)
        self.scroll.setWidget(self.picture)
        layout.addWidget(self.scroll,1)
        controls = QHBoxLayout()
        controls.addStretch()
        self.retry = QPushButton("重试加载",self)
        self.retry.clicked.connect(self.load_original)
        self.retry.hide()
        layout.addWidget(self.retry,0,Qt.AlignHCenter)
        self.save = QPushButton("下载原图",self)
        self.save.setEnabled(row.job is None and row.saved is None and not row.page.window.hub.busy)
        self.save.clicked.connect(self.save_original)
        controls.addWidget(self.save)
        controls.addStretch()
        layout.addLayout(controls)
        row.page.window.scale_layouts()
        self.load_original()

    def load_original(self):
        """异步读取原图路径而非缩略图路径，允许失败后重试。"""
        self.retry.hide()
        self.notice.setText("正在加载原图…")
        window = self.row.page.window
        self.job = window.hub.submit(
            lambda event,progress:window.service.preview(self.row.entry.key,event),self.loaded)

    def loaded(self, data, error):
        """原图在主线程解码，关闭窗口后的回调不再访问控件。"""
        if self.closed or self.row.page.window.closing:
            return
        self.job = None
        pixmap = QPixmap()
        if error or not data or not pixmap.loadFromData(data):
            self.notice.setText(error or "原图无法解码")
            self.retry.show()
            return
        self.pixmap = pixmap
        self.notice.setText(f"{pixmap.width()} × {pixmap.height()}")
        self.fit_picture()

    def fit_picture(self):
        """按窗口可用区域等比例展示原图。"""
        if self.pixmap is None:
            return
        picture = self.pixmap.scaled(
            self.scroll.viewport().size(),Qt.KeepAspectRatio,Qt.SmoothTransformation)
        self.picture.setPixmap(picture)
        self.picture.resize(picture.size())

    def resizeEvent(self, event):
        """窗口大小变化后重新适配原图显示尺寸。"""
        super().resizeEvent(event)
        if hasattr(self,"scroll"):
            self.fit_picture()

    def save_original(self):
        """从预览窗口启动原有下载流程，保存到该资源的正式目标目录。"""
        self.row.clicked()
        self.close()

    def closeEvent(self, event):
        """关闭原图窗口时取消未完成读取，防止迟到回调访问已销毁窗口。"""
        self.closed = True
        if self.job is not None:
            self.row.page.window.hub.cancel(self.job)
        super().closeEvent(event)

    def reject(self):
        """取消读取并结束对话框，避免 closeEvent 与 reject 相互调用。"""
        self.closed = True
        if self.job is not None:
            self.row.page.window.hub.cancel(self.job)
        super().reject()


class ImageGallery(QListWidget):
    def __init__(self, parent):
        """创建图片网格，合并布局变化后再计算居中留白。"""
        super().__init__(parent)
        self.center_timer = QTimer(self)
        self.center_timer.setSingleShot(True)
        self.center_timer.timeout.connect(self.center_items)
        self.setObjectName("galleryContainer")
        self.viewport().setAutoFillBackground(False)

    def resizeEvent(self, event):
        """窗口或滚动条改变可用宽度后重新居中。"""
        super().resizeEvent(event)
        self.center_timer.start(0)

    def center_items(self):
        """固定四列，依据可用宽度分配卡片与间隔，并对称保留少量留白。"""
        margins = self.viewportMargins()
        available = self.viewport().width()+margins.left()+margins.right()
        outer = round(available*.015)
        cell = max(1,(available-2*outer-1)//4)
        gap = max(1,round(cell*.045))
        card_width = max(1,cell-gap)
        px = self.window().metrics.px
        picture_width = max(1,card_width-2*px(10)-2*px(2))
        compact = self.window().selection.mode == "background"
        if compact:
            picture_width = card_width
        picture_height = round(picture_width*9/16)
        card_height = picture_height+px(70 if self.window().selection.mode in ("background","avatar") else 150)
        if compact:
            card_height = picture_height+px(32)
        for index in range(self.count()):
            row = self.itemWidget(self.item(index))
            if row is not None:
                row.picture.setFixedSize(picture_width,picture_height)
                if not compact:
                    card_height = max(card_height,row.layout().minimumSize().height()+px(8))
        grid = QSize(cell,card_height+gap)
        if self.gridSize() != grid:
            self.setGridSize(grid)
        self.setSpacing(0)
        columns = min(4,max(1,self.count()))
        margin = max(0,(available-columns*cell-1)//2)
        # IconMode 从首项的半个卡片间隔处开始判断换行，右侧额外容纳该间隔。
        # 实际卡片位置仍由左边距和四个等宽网格确定，视觉留白保持对称。
        right_margin = max(0,margin-gap) if self.count() >= 4 else margin
        if margins.left() != margin or margins.right() != right_margin:
            self.setViewportMargins(margin,0,right_margin,0)
        for index in range(self.count()):
            item = self.item(index)
            size = QSize(card_width,card_height)
            if item.sizeHint() != size:
                item.setSizeHint(size)
            row = self.itemWidget(item)
            if row is not None:
                row.picture.setFixedSize(picture_width,picture_height)
                row.refresh_preview_size()


class ResourceRow(QFrame):
    def __init__(self, page, entry, legacy=False):
        """构建资源条目，保存该条目的下载、预览和完成状态。"""
        super().__init__(page)
        self.page, self.entry, self.legacy = page, entry, legacy
        self.image_tile = not legacy and entry.kind in ("background","avatar")
        self.compact_card = not legacy and entry.kind == "background"
        if self.image_tile:
            self.setObjectName("resourceCard")
        self.job, self.saved = None, None
        self.download_selection: Optional[Selection] = None
        self.target_context: Optional[tuple[str, str]] = None
        self.preview_requested = False
        self.preview_pixmap = QPixmap(str(APP_ROOT / "icons/loading.png"))
        self.metadata_ready = not legacy
        self.title = entry if legacy else entry.title
        layout = QVBoxLayout(self) if self.image_tile else QHBoxLayout(self)
        self.picture = PreviewLabel("预览", self) if self.image_tile else QLabel("预览", self)
        if self.image_tile:
            self.picture.setCursor(Qt.PointingHandCursor)
            self.picture.clicked.connect(self.open_preview)
        self.picture.setAlignment(Qt.AlignCenter)
        if not self.image_tile:
            self.picture.setFixedSize(page.window.metrics.px(124),page.window.metrics.px(90))
        self.picture.setObjectName("resourcePreview")
        self.picture.setVisible(True)
        self.refresh_preview_size()
        layout.addWidget(self.picture)
        content = QVBoxLayout()
        self.content_layout = content
        self.name = ElidedResourceLabel(self) if self.compact_card else QLabel(self.title, self)
        if self.compact_card:
            self.name.setText(self.title)
        self.name.setWordWrap(not self.image_tile)
        self.name.setTextFormat(Qt.PlainText)
        self.name.setObjectName("resourceName")
        content.addWidget(self.name)
        self.detail = QLabel(entry if legacy else entry.key, self)
        self.detail.setTextFormat(Qt.PlainText)
        self.detail.setWordWrap(True)
        if not legacy and entry.kind == "model":
            self.detail.setText(f"包版本：{Path(entry.filename).stem} · {entry.model_id}")
        self.detail.setObjectName("resourceDetail")
        self.detail.setVisible(not self.image_tile)
        self.name.setToolTip(self.title + "\n" + (entry if legacy else entry.key))
        if self.image_tile:
            self.name.setFixedHeight(page.window.metrics.px(45))
            self.name.setVisible(page.show_image_names)
            self.picture.setToolTip(self.title)
        content.addWidget(self.detail)
        # self.status = QLabel("V2 · 下载后安装" if legacy else (
        #     "V3 · ZIP 下载后校验，暂不安装" if entry.kind == "model" else "下载原图"),self)
        # self.status.setVisible(not self.image_tile)
        # self.status.setWordWrap(True)
        # self.status.setTextFormat(Qt.PlainText)
        # 默认提示保持隐藏，但保留下载进度和错误状态控件。
        self.status = QLabel(self)
        self.status.setWordWrap(True)
        self.status.hide()
        content.addWidget(self.status)
        self.bar = QProgressBar()
        self.bar.setTextVisible(False)
        self.bar.hide()
        content.addWidget(self.bar)
        layout.addLayout(content, 1)
        buttons = QHBoxLayout() if self.image_tile else QVBoxLayout()
        self.button = QPushButton("下载")
        if self.image_tile:
            self.button.setObjectName("downloadItemBtn")
        self.button.setEnabled(not legacy)
        self.button.clicked.connect(self.clicked)
        buttons.addWidget(self.button)
        self.locate = QPushButton("打开位置")
        self.locate.hide()
        self.locate.clicked.connect(self.open_location)
        buttons.addWidget(self.locate)
        layout.addLayout(buttons)
        if self.compact_card:
            # 复用下载控件与状态，将旧纵向内容收进单行操作条。
            layout.removeItem(content)
            layout.removeItem(buttons)
            for widget in (self.name,self.detail,self.status,self.bar):
                content.removeWidget(widget)
            for widget in (self.button,self.locate):
                buttons.removeWidget(widget)
            content.deleteLater()
            buttons.deleteLater()
            self.status.hide()
            self.status.deleteLater()
            self.status = ElidedResourceLabel(self)
            self.status.setObjectName("resourceName")
            self.status.hide()
            self.bottom_bar = QFrame(self)
            self.bottom_bar.setObjectName("cardBottomBar")
            bottom = QHBoxLayout(self.bottom_bar)
            bottom.addWidget(self.name)
            bottom.addWidget(self.status)
            bottom.addStretch()
            bottom.addWidget(self.button,0,Qt.AlignRight)
            bottom.addWidget(self.locate,0,Qt.AlignRight)
            self.locate.setObjectName("downloadItemBtn")
            self.name.setMinimumWidth(0)
            self.status.setMinimumWidth(0)
            self.name.setSizePolicy(QSizePolicy.Ignored,QSizePolicy.Preferred)
            self.status.setSizePolicy(QSizePolicy.Ignored,QSizePolicy.Preferred)
            bottom.setStretch(0,1)
            bottom.setStretch(1,1)
            self.picture.setProperty("edgePreview",True)
            self.setProperty("compactCard",True)
            layout.addWidget(self.bottom_bar)
            self.scale_card()

    def scale_card(self):
        """为上图下栏保留独立布局规则，所有操作条尺寸按屏幕比例换算。"""
        px = self.page.window.metrics.px
        self.layout().setContentsMargins(0,0,0,0)
        self.layout().setSpacing(0)
        self.bottom_bar.setFixedHeight(px(32))
        self.bottom_bar.layout().setContentsMargins(px(8),px(4),px(8),px(4))
        self.bottom_bar.layout().setSpacing(px(4))
        self.name.setMinimumHeight(0)
        self.name.setMaximumHeight(16777215)

    def open_preview(self):
        """以独立原图窗口展示当前图片，保留列表所在分页。"""
        dialog = OriginalPreview(self)
        dialog.show()

    def resizeEvent(self, event):
        """图片网格中的长文件名中间省略，保留尾部编号，完整名称放在悬停提示。"""
        super().resizeEvent(event)
        if self.image_tile and not self.compact_card:
            self.name.setText(self.name.fontMetrics().elidedText(self.title,Qt.ElideMiddle,max(1,self.name.width())))

    def preview(self, data):
        """读取或显示资源预览；失败时由界面提供占位和重试。"""
        pixmap = QPixmap()
        if data and pixmap.loadFromData(data):
            self.preview_pixmap = pixmap
            self.refresh_preview_size()
        else:
            self.preview_pixmap = QPixmap(str(APP_ROOT / "icons/loading.png"))
            self.refresh_preview_size()

    def refresh_preview_size(self):
        """以保留的原始缩略图适配卡片新尺寸，避免放大已经缩小的预览。"""
        if self.preview_pixmap is not None:
            self.picture.setPixmap(self.preview_pixmap.scaled(self.picture.size(),Qt.KeepAspectRatio,Qt.SmoothTransformation))

    def installation_key(self, choice: Selection) -> tuple[str, str, str]:
        """用接收角色、来源角色和资源标识区分本次会话的安装结果。"""
        target = choice.source if choice.mode == "new" else choice.target_id
        resource = "v2:" + self.entry if self.legacy else "v3:" + self.entry.key
        return target, choice.source, resource

    def update_target(self) -> None:
        """接收角色变化时刷新安装状态，不重建卡片或影响预览与滚动位置。"""
        if self.image_tile or self.job is not None:
            return
        window = self.page.window
        choice = window.selection
        context = (choice.mode, choice.target_id)
        changed = context != self.target_context
        self.target_context = context
        saved = window.installed_models.get(self.installation_key(choice))
        if saved is not None and not saved.is_dir():
            saved = None
        if saved is None and not self.legacy and choice.mode == "existing" and choice.target_id:
            saved = find_installed_v3(PROJECT_ROOT / "live2d_related" / choice.target_id, self.entry.model_id)
        if saved is None and self.legacy and self.metadata_ready and choice.mode == "existing" and choice.target_id:
            title = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", self.title).rstrip(". ") or self.entry
            character = PROJECT_ROOT / "live2d_related" / choice.target_id
            saved = next((path for path in (character / "extra_model" / title,
                                           character / "live2D_model" / title) if path.is_dir()), None)
        if changed or saved != self.saved:
            self.saved = saved
            self.bar.hide()
            self.locate.setVisible(saved is not None)
            self.status.setVisible(saved is not None)
            self.status.setText("此服装已安装" if saved else "")
            self.status.setToolTip(str(saved) if saved else "")
            self.button.setText("已安装" if saved else "下载")
        self.button.setEnabled(not window.hub.busy and self.metadata_ready and self.saved is None)

    def clicked(self) -> None:
        """开始快照化下载任务，或请求停止正在执行的任务。"""
        hub = self.page.window.hub
        if self.job is not None:
            hub.cancel(self.job)
            self.button.setEnabled(False)
            self.status.setText("正在停止，请稍候…")
            return
        window = self.page.window
        if hub.busy:
            return
        choice = window.selection  # 不可变任务快照
        if self.image_tile and (destination(self.entry,window.zip_root) / self.entry.filename).exists():
            answer = QMessageBox.warning(window,"确认覆盖",
                "检测到同名图片文件已存在，继续下载将覆盖。",
                QMessageBox.Yes | QMessageBox.Cancel,QMessageBox.Cancel)
            if answer != QMessageBox.Yes:
                return
        if self.legacy or self.entry.kind == "model":
            choice = window.prepare_model_download()
            if choice is None:
                return
            self.update_target()
            if self.saved is not None:
                return
            self.download_selection = choice
        self.status.show()
        if self.compact_card:
            self.name.hide()
        if self.image_tile:
            self.page.panels[self.entry.kind][2].center_timer.start(0)
        self.button.setText("停止")
        self.bar.show()
        if self.compact_card:
            self.bar.hide()
        self.bar.setRange(0, 0)
        self.status.setText("等待下载…")
        if self.legacy:
            action = lambda event, progress: window.legacy.download(self.entry, self.title, choice, event, progress)
        elif self.entry.kind == "model":
            action = lambda event, progress: window.installer.download(self.entry, choice, window.zip_root, event, progress)
        else:
            folder = destination(self.entry, window.zip_root)
            action = lambda event, progress: window.service.download(self.entry, folder, event, progress)
        self.job = hub.submit(action, self.finished, self.progress, download=True)
        self.button.setEnabled(True)

    def progress(self, done, total):
        """显示字节或文件数进度；请求取消后保留“正在停止”提示。"""
        if self.button.isEnabled():
            unit = "个文件" if self.legacy else "KiB"
            self.status.setText(f"下载中：{done:,} / {total:,} {unit}" if total else f"已下载 {done:,} {unit}")
        self.bar.setRange(0, 100 if total else 0)
        if total:
            self.bar.setValue(min(100, int(done / total * 100)))
        if not self.legacy and self.entry.kind == "model" and total > 0 and done >= total and self.button.isEnabled():
            self.status.setText("下载完成，正在校验并安装…")
            self.bar.setRange(0, 0)

    def finished(self, value, error):
        """显示下载或安装结果，取消和失败时恢复重试操作。"""
        self.job = None
        if self.page.window.closing:
            return
        self.status.show()
        if self.compact_card:
            self.name.hide()
        if self.image_tile:
            self.page.panels[self.entry.kind][2].center_timer.start(0)
        self.bar.setRange(0, 100)
        if error:
            self.status.setText(error)
            self.button.setText("重试")
            self.button.setEnabled(True)
        else:
            self.saved = value.path if isinstance(value, InstallResult) else Path(value)
            if self.download_selection is not None:
                self.page.window.installed_models[self.installation_key(self.download_selection)] = self.saved
                self.page.window.model_installed(self.download_selection)
            self.bar.setValue(100)
            status = "安装完成" if self.legacy else (
                "已下载 · SHA-256 校验通过" if self.entry.sha256 else
                "已下载 · 未提供校验值" if self.entry.kind == "model" else "原图已保存")
            if isinstance(value, InstallResult):
                status = "此服装已安装" if value.already_installed else "安装完成"
                if value.description_missing:
                    status += " · 暂缺角色描述，需自行补齐"
                elif value.new_character:
                    status += " · 重启后与角色对话吧~"
                if not self.entry.sha256:
                    status += " · 未提供校验值"
            self.status.setText(status)
            if self.image_tile:
                self.page.saved_images[self.entry.key] = self.saved
            self.status.setToolTip(str(self.saved))
            self.button.setText("已安装" if isinstance(value, InstallResult) and value.already_installed else "已完成")
            self.button.setEnabled(False)
            self.locate.show()
            if self.compact_card:
                self.button.hide()
            if hasattr(self, "list_item") and not self.image_tile:
                self.list_item.setSizeHint(QSize(self.page.window.metrics.px(400), self.page.window.metrics.px(260)))

    def open_location(self):
        """使用系统文件管理器打开完成文件所在目录。"""
        if self.saved:
            folder = self.saved if self.saved.is_dir() else self.saved.parent
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))


class ResourcePage(QWidget):
    def __init__(self, window):
        """构建分栏资源列表及按可见范围加载预览的定时器。"""
        super().__init__()
        self.window, self.generation = window, 0
        self.rows, self.panels, self.states = [], {}, {}
        self.image_entries, self.saved_images = [], {}
        self.image_mode, self.image_page, self.page_size = False, 0, 24
        self.show_image_names = False
        layout = QVBoxLayout(self)
        self.heading = QLabel()
        self.heading.setWordWrap(True)
        self.heading.setObjectName("downloadHeading")
        layout.addWidget(self.heading)
        tools = QHBoxLayout()
        # self.search = QLineEdit()
        # self.search.setPlaceholderText("筛选名称或资源 ID")
        # self.search.textChanged.connect(self.filter_rows)
        self.refresh_buttons = []
        # tools.addWidget(self.search, 1)
        # 刷新入口改在各版本容器内。
        # layout.addLayout(tools)
        self.columns = QHBoxLayout()
        layout.addLayout(self.columns, 1)
        self.pager = QWidget(self)
        pagination = QHBoxLayout(self.pager)
        self.previous_page = QPushButton("上一页")
        self.next_page = QPushButton("下一页")
        self.page_label = QLabel()
        self.previous_page.setObjectName("pageBtn")
        self.next_page.setObjectName("pageBtn")
        self.page_label.setObjectName("pageIndicator")
        self.page_label.setAlignment(Qt.AlignCenter)
        self.previous_page.clicked.connect(lambda:self.turn_page(-1))
        self.next_page.clicked.connect(lambda:self.turn_page(1))
        pagination.addStretch()
        pagination.addWidget(self.previous_page)
        pagination.addWidget(self.page_label)
        pagination.addWidget(self.next_page)
        pagination.addStretch()
        layout.addWidget(self.pager)
        self.pager.hide()
        self.back = QPushButton("返回选择角色")
        self.back.clicked.connect(self.go_back)
        layout.addWidget(self.back)
        self.preview_timer = QTimer(self)
        self.preview_timer.setInterval(180)
        self.preview_timer.timeout.connect(self.load_visible_previews)
        self.preview_timer.start()

    def apply_styles(self):
        """按资源类型应用 appearance.py 中对应的页面样式。"""
        name = {"background":"backgrounds", "avatar":"avatars"}.get(self.window.selection.mode,"models")
        css = self.window.metrics.page_stylesheet(name)
        if name == "models":
            index = next((i for i,button in enumerate(self.window.characters.band_buttons) if button.isChecked()),0)
            accent = self.window.metrics.band_accent(index).name()
            css += f"QLabel, QGroupBox, QGroupBox::title, QPushButton {{color:{accent};}} QLabel#resourceDetail {{color:{accent};}} QLabel#resourcePreview {{color:{accent};}} QProgressBar::chunk {{background:{accent};}}"
        self.setStyleSheet(css)

    def update_target(self) -> None:
        """仅更新模型用途、接收角色与各卡片安装状态，保留资源查询和列表位置。"""
        choice = self.window.selection
        if choice.mode not in ("new", "existing"):
            return
        target = (f"添加到已有角色"
                  if choice.mode == "existing" else "加入新角色")
        self.heading.setText(f"{CHARACTERS[choice.source]['display_name']} · {target}")
        for _, _, row in self.rows:
            row.update_target()

    def load(self):
        """重建当前资源页，独立查询 V2、V3 或图片目录。"""
        if self.window.hub.busy:
            return
        self.generation += 1
        generation = self.generation
        self.window.hub.cancel_reads()
        self.rows, self.panels, self.states = [], {}, {}
        while self.columns.count():
            self.columns.takeAt(0).widget().deleteLater()
        choice = self.window.selection
        background = choice.mode in ("background","avatar")
        self.heading.setVisible(not background)
        # self.search.setVisible(not background)
        self.refresh_buttons = []
        self.apply_styles()
        self.image_mode = choice.mode in ("background","avatar")
        self.image_entries, self.image_page = [], 0
        self.pager.setVisible(self.image_mode)
        self.previous_page.setEnabled(False)
        self.next_page.setEnabled(False)
        self.page_label.setText("正在读取目录…")
        # self.search.blockSignals(True)
        # self.search.clear()
        # self.search.blockSignals(False)
        if choice.mode == "background":
            self.heading.setText("背景图片 · 下载原图到 live2d_related")
        elif choice.mode == "avatar":
            self.heading.setText(CHARACTERS[choice.source]["display_name"] + " · 聊天头像")
        else:
            self.update_target()
        self.back.setText("返回角色选择")
        self.back.setVisible(choice.mode != "background")
        kinds = ["background"] if choice.mode == "background" else ["avatar"] if choice.mode == "avatar" else ["v2", "model"]
        for kind in kinds:
            if kind == "v2" and not isinstance(CHARACTERS[choice.source]["bestdori_index"], int):
                continue
            box = QFrame() if background else QGroupBox({"v2": "Live2D V2 · Girls Band Party", "model": "Live2D V3 · OurNotes", "avatar": "WebUI 聊天头像"}[kind])
            if background:
                box.setObjectName("backgroundGalleryPanel")
            body = QVBoxLayout(box)
            status = QLabel("正在读取目录…")
            status.setObjectName("pageCountNotice")
            if background:
                status.setObjectName("galleryTitle")
            status.setWordWrap(True)
            if background:
                header = QHBoxLayout()
                header.addWidget(status,1)
                self.catalog_retry = QPushButton("重试加载",box)
                self.catalog_retry.setObjectName("actionButton")
                self.catalog_retry.clicked.connect(self.load)
                self.catalog_retry.hide()
                header.addWidget(self.catalog_retry)
                self.names_toggle = QPushButton("显示图片名称",box)
                self.names_toggle.setObjectName("actionButton")
                self.names_toggle.setCheckable(True)
                self.names_toggle.setChecked(self.show_image_names)
                self.names_toggle.toggled.connect(self.toggle_image_names)
                header.addWidget(self.names_toggle)
                body.addLayout(header)
            else:
                header = QHBoxLayout()
                header.addWidget(status,1)
                refresh = QPushButton("刷新",box)
                refresh.setObjectName("refreshButton")
                refresh.clicked.connect(self.load)
                self.refresh_buttons.append(refresh)
                header.addWidget(refresh,0,Qt.AlignRight)
                body.addLayout(header)
            listing = ImageGallery(box) if self.image_mode else QListWidget(box)
            if self.image_mode:
                listing.setViewMode(QListWidget.IconMode)
                listing.setResizeMode(QListWidget.Adjust)
                listing.setMovement(QListWidget.Static)
                listing.setWrapping(True)
                listing.setSpacing(self.window.metrics.px(10))

            listing.setVerticalScrollMode(QListWidget.ScrollPerPixel)
            listing.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOn)
            body.addWidget(listing)
            self.columns.addWidget(box, 1)
            self.panels[kind] = (box, status, listing)
            self.states[kind] = "loading"
            if kind == "v2":
                action = lambda event, progress: self.window.legacy.catalog(choice.source, event)
            else:
                action = lambda event, progress, k=kind: self.window.service.catalog(k, choice.source, event)
            self.window.hub.submit(action, lambda result, error, k=kind: self.catalog_ready(generation, k, result, error))

    def catalog_ready(self, generation, kind, result, error):
        """接收当前代次的目录结果；只隐藏确认无资源的版本栏。"""
        if generation != self.generation or self.window.closing:
            return
        box, status, listing = self.panels[kind]
        if error:
            self.states[kind] = "error"
            status.setText(error + ("；可点击重试加载" if self.image_mode else "；可点击刷新重试"))
            if self.image_mode:
                self.catalog_retry.show()
        else:
            self.states[kind] = "ready" if result else "empty"
            status.setText(f"共 {len(result)} 项" if result else "暂无可下载资源")
            if self.image_mode:
                self.image_entries = result
                self.render_image_page()
                return
            for entry in result:
                row = ResourceRow(self, entry, kind == "v2")
                item = QListWidgetItem()
                item.setSizeHint(QSize(self.window.metrics.px(400), self.window.metrics.px(200)))
                row.list_item = item
                listing.addItem(item)
                listing.setItemWidget(item, row)
                self.rows.append((listing, item, row))
                row.update_target()
        # 只有成功确认空目录才隐藏；错误和未完成查询保持可见。
        nonempty = any(state == "ready" for state in self.states.values())
        for key, (panel, _, _) in self.panels.items():
            panel.setVisible(not (self.states[key] == "empty" and nonempty))
        # self.filter_rows()  # 暂停名称筛选
        self.window.scale_layouts()
        self.busy_changed(self.window.hub.busy)

    def toggle_image_names(self, checked):
        """切换图片文件名显示，保留当前页、已加载预览及下载任务。"""
        self.show_image_names = checked
        for listing,item,row in self.rows:
            row.name.setVisible(checked and (not row.compact_card or row.status.isHidden()))
            listing.center_timer.start(0)

    # def filter_rows(self):
    #     """按名称或稳定资源标识筛选已加载的列表条目。"""
    #     if self.image_mode:
    #         self.image_page = 0
    #         self.render_image_page()
    #         return
    #     query = self.search.text().strip().lower()
    #     for listing, item, row in self.rows:
    #         item.setHidden(query not in (row.title + " " + (row.entry if row.legacy else row.entry.key)).lower())

    def turn_page(self, direction):
        """下载空闲时翻页；旧页预览回调通过代次校验失效。"""
        if not self.window.hub.busy:
            self.image_page += direction
            self.render_image_page()

    def render_image_page(self):
        """图片目录按全局筛选后分页，只为当前页建立资源管理器式网格。"""
        if not self.panels or self.window.hub.busy:
            return
        kind = next(iter(self.panels))
        if self.states[kind] not in ("ready","empty"):
            return
        self.generation += 1
        self.window.hub.cancel_reads()
        box,status,listing = self.panels[kind]
        # 暂停筛选，直接对完整目录分页。
        entries = self.image_entries
        pages = max(1,(len(entries)+self.page_size-1)//self.page_size)
        self.image_page = max(0,min(self.image_page,pages-1))
        listing.clear()
        self.rows = []
        for entry in entries[self.image_page*self.page_size:(self.image_page+1)*self.page_size]:
            row = ResourceRow(self,entry)
            item = QListWidgetItem()

            row.list_item = item
            listing.addItem(item)
            listing.setItemWidget(item,row)
            self.rows.append((listing,item,row))
            if entry.key in self.saved_images:
                row.finished(self.saved_images[entry.key],"")
        listing.scrollToTop()
        listing.center_timer.start(0)
        status.setText("OurNotes 故事模式背景图" if kind == "background" else "WebUI 角色聊天头像")
        self.page_label.setText(f"{self.image_page+1} / {pages}")
        self.previous_page.setEnabled(self.image_page > 0)
        self.next_page.setEnabled(self.image_page+1 < pages)
        self.window.scale_layouts()

    def load_visible_previews(self):
        """仅为可见条目加载预览，避免一次下载全部背景图片。"""
        if not self.isVisible() or self.window.closing:
            return
        generation = self.generation
        for listing, item, row in self.rows:
            if row.preview_requested or item.isHidden() or not listing.isVisible():
                continue
            if not listing.viewport().rect().intersects(listing.visualItemRect(item)):
                continue
            if not row.legacy and not row.entry.preview_key:
                continue
            row.preview_requested = True
            if row.legacy:
                action = lambda event, progress, costume=row.entry: self.window.legacy.metadata(costume, event)
            else:
                action = lambda event, progress, key=row.entry.preview_key: self.window.service.preview(key, event)
            def done(value, error, widget=row):
                """接收后台结果；先检查窗口或页面代次，再更新对应控件。"""
                if generation != self.generation or self.window.closing:
                    return
                if widget.legacy:
                    widget.metadata_ready = True
                    widget.button.setEnabled(not self.window.hub.busy)
                    if not error:
                        widget.title = value[0]
                        widget.name.setText(value[0])
                        widget.preview(value[1])
                        widget.update_target()
                    else:
                        widget.preview(None)
                else:
                    widget.preview(value if not error else None)
            self.window.hub.submit(action, done)

    def go_back(self):
        """仅在下载完全停止后返回，并使旧页面回调失效。"""
        if self.window.hub.busy:
            return
        self.generation += 1
        self.window.hub.cancel_reads()
        if self.window.selection.mode == "background":
            self.window.choose_mode(self.window.model_mode)
        else:
            self.window.stack.setCurrentWidget(self.window.characters)
            self.window.characters.carousel.changed()

    def busy_changed(self, busy):
        """锁定返回、刷新和其他下载按钮，保留当前任务的停止按钮。"""
        self.back.setEnabled(not busy)
        for button in self.refresh_buttons:
            button.setEnabled(not busy)
        if self.image_mode:
            # self.search.setEnabled(not busy)
            self.pager.setEnabled(not busy)
        # 首版串行下载，避免同一 V2 角色并发安装与缓存清理冲突。
        for _, _, row in self.rows:
            if row.job is None and row.saved is None:
                row.button.setEnabled(not busy and row.metadata_ready)


class DownloadWizardWindow(QDialog):
    def __init__(self, characters, service=None, legacy=None):
        """组装四入口下载向导，统一管理任务、页面和关闭生命周期。"""
        super().__init__()
        self.character_list = characters
        self.selection = Selection()
        self.installed_models: dict[tuple[str, str, str], Path] = {}
        self.service = service or ResourceService()
        self.legacy = legacy or LegacyService()
        self.installer = V3Installer(self.service)
        self.hub = JobHub(self)
        self.closing = False
        self.background_color = QColor("#F0F4F9")
        self.background_animation = QVariantAnimation(self)
        self.background_animation.setDuration(380)
        self.background_animation.setEasingCurve(QEasingCurve.InOutCubic)
        self.background_animation.valueChanged.connect(self.set_background_color)
        self.watermark_logos, self.watermark_weights = {}, {}
        self.watermark_from, self.watermark_target = {}, None
        self.watermark_animation = QVariantAnimation(self)
        self.watermark_animation.setDuration(380)
        self.watermark_animation.setEasingCurve(QEasingCurve.InOutCubic)
        self.watermark_animation.valueChanged.connect(self.blend_watermarks)
        # V3 每次任务清理自己的 ZIP 与解包文件，会话空目录在关闭时移除。
        self.zip_root = CACHE_ROOT / "resource-downloads" / uuid.uuid4().hex
        self.setWindowTitle("数字小祥资源下载器")
        geometry = QApplication.primaryScreen().availableGeometry()
        self.metrics = ScreenMetrics(geometry.width(), geometry.height())
        self.resize(*self.metrics.window_size(QApplication.primaryScreen()))
        self.setStyleSheet(self.metrics.stylesheet())
        root = QVBoxLayout(self)
        self.model_mode = "new"
        self.navigation = QFrame(self)
        self.navigation.setObjectName("topNavContainer")
        navigation = QHBoxLayout(self.navigation)
        self.main_tab_track = QFrame(self.navigation)
        self.main_tab_track.setObjectName("mainTabTrack")
        main_tabs = QHBoxLayout(self.main_tab_track)
        self.type_group = QButtonGroup(self)
        self.type_group.setExclusive(True)
        self.type_tabs = {}
        for key,text in (("model","Live2D模型"),("avatar","聊天头像"),("background","背景图片")):
            button = QPushButton(self.main_tab_track)
            button.setProperty("topTab", True)
            button.setText(text)
            button.setCheckable(True)
            button.clicked.connect(lambda checked,k=key:self.choose_mode(self.model_mode if k == "model" else k))
            self.type_group.addButton(button)
            self.type_tabs[key] = button
            shadow = QGraphicsDropShadowEffect(button)
            shadow.setColor(QColor(40,50,80,35))
            shadow.setEnabled(False)
            button.setGraphicsEffect(shadow)
            button.toggled.connect(shadow.setEnabled)
            main_tabs.addWidget(button)
        navigation.addWidget(self.main_tab_track)
        navigation.addStretch(1)
        self.model_options = QFrame(self.navigation)
        self.model_options.setObjectName("subModeTrack")
        options = QHBoxLayout(self.model_options)
        self.model_group = QButtonGroup(self)
        self.model_group.setExclusive(True)
        self.mode_tabs = {}
        for key,text in (("new","加入新角色"),("existing","为已有角色添加服装")):
            button = QPushButton(self.model_options)
            button.setProperty("subMode", True)
            button.setText(text)
            button.setCheckable(True)
            button.clicked.connect(lambda checked,k=key:self.choose_mode(k))
            self.model_group.addButton(button)
            self.mode_tabs[key] = button
            options.addWidget(button)
        navigation.addWidget(self.model_options)
        root.addWidget(self.navigation)
        self.stack = QStackedWidget()
        # 透明子页切换不会自动使顶层背景全部失效，需主动重绘导航与页边留白。
        self.stack.currentChanged.connect(self.update)
        root.addWidget(self.stack)
        self.characters = CharacterPage(self)
        self.resources = ResourcePage(self)
        self.receiver_bar = QWidget(self)
        receiver_layout = QHBoxLayout(self.receiver_bar)
        receiver_layout.addWidget(QLabel("添加到本地角色：", self.receiver_bar))
        receiver_layout.addWidget(self.characters.receiver, 1)
        root.insertWidget(1, self.receiver_bar)
        self.selection_notice = QLabel(self)
        self.selection_notice.setObjectName("selectionNotice")
        self.selection_notice.setWordWrap(True)
        self.selection_notice.setTextFormat(Qt.PlainText)
        self.selection_notice.hide()
        self.selection_notice_timer = QTimer(self)
        self.selection_notice_timer.setSingleShot(True)
        self.selection_notice_timer.setInterval(4000)
        self.selection_notice_timer.timeout.connect(self.selection_notice.hide)
        root.insertWidget(2, self.selection_notice)
        for page in (self.characters, self.resources):
            self.stack.addWidget(page)
            page.setProperty("themeSurface", True)
        self.stack.currentChanged.connect(self.update_installation_controls)
        for widget in self.findChildren(QWidget):
            if type(widget) in (QWidget, QStackedWidget):
                widget.setProperty("themeSurface", True)
        self.hub.busy_changed.connect(self.resources.busy_changed)
        self.hub.busy_changed.connect(self.update_navigation)
        self.close_timer = QTimer(self)
        self.close_timer.setInterval(100)
        self.close_timer.timeout.connect(self.finish_close)
        self.scale_screen(QApplication.primaryScreen(), resize=False)
        self.characters.reset()
        self.choose_mode("new")

    def transition_background(self, band_index):
        """从当前底色平滑过渡到淡化的乐队色，快速切换时替换旧动画。"""
        def get_perceptual_target(accent: QColor) -> QColor:
            # 计算人眼感知灰度: 0.299R + 0.587G + 0.114B
            lum = (0.299 * accent.red() + 0.587 * accent.green() + 0.114 * accent.blue()) / 255.0

            # 颜色越暗（如深蓝、深红），稀释比例越小；颜色越亮（如柠檬黄），保留稍多一点饱和度
            ratio = 0.10 if lum < 0.4 else 0.18

            return QColor(
                round(250 * (1 - ratio) + accent.red() * ratio),
                round(252 * (1 - ratio) + accent.green() * ratio),
                round(255 * (1 - ratio) + accent.blue() * ratio)
            )

        accent = QColor(BAND_COLORS[band_index])
        target = get_perceptual_target(accent)

        self.background_animation.stop()
        self.background_animation.setStartValue(QColor(self.background_color))
        self.background_animation.setEndValue(target)
        self.background_animation.start()
        self.watermark_animation.stop()
        self.watermark_from = dict(self.watermark_weights)
        self.watermark_target = band_index if self.watermark_logo(band_index) is not None else None
        self.watermark_animation.setStartValue(0.0)
        self.watermark_animation.setEndValue(1.0)
        self.blend_watermarks(0.0)
        self.watermark_animation.start()

    def watermark_logo(self, band_index):
        """缓存本地队徽的反白透明剪影，无队徽的 Others 不显示水印。"""
        if band_index not in self.watermark_logos:
            logo = QPixmap(str(APP_ROOT / "assets/band_logo" / (BANDS[band_index][0]+".png")))
            if logo.isNull():
                self.watermark_logos[band_index] = None
            else:
                painter = QPainter(logo)
                painter.setCompositionMode(QPainter.CompositionMode_SourceIn)
                # painter.fillRect(logo.rect(),Qt.white)
                painter.end()
                self.watermark_logos[band_index] = logo
        return self.watermark_logos[band_index]

    def blend_watermarks(self, value):
        """从当前混合权重交叉淡入新队徽，快速切换也不发生水印跳变。"""
        if value is None:
            return
        progress = float(value)
        self.watermark_weights = {key:weight*(1-progress) for key,weight in self.watermark_from.items()
                                  if weight*(1-progress) > .001}
        if self.watermark_target is not None:
            key = self.watermark_target
            self.watermark_weights[key] = self.watermark_weights.get(key,0)+progress
        self.update()

    def set_background_color(self, color):
        """只刷新背景绘制，不在动画帧中重设样式表或重新布局。"""
        if isinstance(color,QColor) and color.isValid():
            self.background_color = QColor(color)
            self.update()

    def paintEvent(self, event):
        """仅绘制窗口背景：纵向底色、卡片中心柔光、淡斜纹及乐队剪影水印。"""
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.setRenderHint(QPainter.SmoothPixmapTransform)
        gradient = QLinearGradient(0,0,0,self.height())
        background_page = self.selection.mode in ("background","avatar") and self.stack.currentWidget() is self.resources
        base_color = QColor("#F0F4F9") if background_page else self.background_color
        gradient.setColorAt(0,base_color)
        gradient.setColorAt(1,base_color.darker(106))
        painter.fillRect(self.rect(),gradient)
        center = QPointF(self.width()*.5,self.height()*.54)
        if self.stack.currentWidget() is self.characters:
            carousel = self.characters.carousel
            point = carousel.mapTo(self,carousel.rect().center())
            center = QPointF(point)
        glow = QRadialGradient(center,max(self.width()*.58,self.height()*.64))
        glow.setColorAt(0,QColor(255,255,255,210))
        glow.setColorAt(.35,QColor(255,255,255,125))
        glow.setColorAt(1,QColor(255,255,255,0))
        painter.fillRect(self.rect(),glow)
        painter.setPen(QPen(QColor(75,88,124,13),max(.5,self.metrics.px(1))))
        spacing = self.metrics.px(30)
        for x in range(-self.height(),self.width()+spacing,spacing):
            painter.drawLine(QPointF(x,0),QPointF(x+self.height()*.5,self.height()))
        for index,weight in (() if background_page else self.watermark_weights.items()):
            logo = self.watermark_logo(index)
            if logo is None:
                continue
            width = self.width()*0.88
            height = width*logo.height()/logo.width()
            if height > self.height()*.72:
                width *= self.height()*.72/height
                height = self.height()*.72
            painter.setOpacity(.25*weight)
            painter.drawPixmap(QRectF(center.x()-width/2,center.y()-height/2,width,height),
                               logo,QRectF(logo.rect()))

    def showEvent(self, event):
        """首次显示后监听屏幕切换，让不同分辨率显示器使用各自比例。"""
        super().showEvent(event)
        handle = self.windowHandle()
        if handle and not getattr(self, "_screen_connected", False):
            self._screen_connected = True
            handle.screenChanged.connect(self.scale_screen)
            self.scale_screen(handle.screen(), resize=False)

    def scale_screen(self, screen, resize=True):
        """用 Qt 逻辑可用分辨率同步窗口、字体、控件及布局，不重复乘系统 DPI。"""
        geometry = screen.availableGeometry()
        self.metrics = ScreenMetrics(geometry.width(), geometry.height())
        px = self.metrics.px
        self.setStyleSheet(self.metrics.stylesheet())
        self.setMinimumSize(round(geometry.width()*.45),round(geometry.height()*.55))
        if resize:
            self.resize(*self.metrics.window_size(screen))
        self.characters.setStyleSheet(self.metrics.page_stylesheet("characters"))
        self.characters.update_footer_logo()
        self.resources.apply_styles()
        self.scale_layouts()
        self.characters.carousel.setMinimumHeight(round(geometry.height()*.3))
        for button in self.characters.band_buttons:
            button.setFixedHeight(px(65))
            button.setIconSize(QSize(px(120),px(54))) #TODO
        for _, item, row in self.resources.rows:

            if row.image_tile:
                row.name.setFixedHeight(px(45))

            else:
                row.picture.setFixedSize(px(124),px(90))
                item.setSizeHint(QSize(px(400),px(260 if row.saved else 200)))
        if self.resources.image_mode:
            for _,_,listing in self.resources.panels.values():
                listing.setSpacing(px(10))

                listing.center_timer.start(0)
        self.characters.carousel.update()

    def scale_layouts(self):
        """同步静态和异步生成布局的间距，避免资源列表保留固定像素。"""
        px = self.metrics.px
        for layout in self.findChildren(QLayout):
            layout.setContentsMargins(px(10),px(10),px(10),px(10))
            layout.setSpacing(px(8))
        # 顶栏使用紧凑边距，不能套用内容区的通用边距。
        # 与角色页三层内容边距对齐，并扣除顶栏 QSS 的左右 padding。
        inset = max(0,3*px(10)-px(12))
        self.navigation.layout().setContentsMargins(inset,0,inset,0)
        for track in (self.main_tab_track,self.model_options):
            track.layout().setContentsMargins(0,0,0,0)
            track.layout().setSpacing(px(2))
        for button in self.type_tabs.values():
            button.graphicsEffect().setBlurRadius(px(8))
            button.graphicsEffect().setOffset(0,px(1))
        for row in self.findChildren(ResourceRow):
            if row.compact_card:
                row.scale_card()
            elif not row.image_tile:
                row.content_layout.setContentsMargins(px(8),0,px(8),0)
                row.content_layout.setSpacing(px(4))
                row.content_layout.setAlignment(Qt.AlignVCenter)

    def set_navigation_band(self, index):
        """切换乐队时同步两组导航强调色，复用底部主按钮的配色规则。"""
        for button in (*self.type_tabs.values(),*self.mode_tabs.values()):
            button.setProperty("bandIndex",str(index))
            button.style().unpolish(button)
            button.style().polish(button)
            button.update()

    def refresh_targets(self) -> list[LocalTarget]:
        """刷新磁盘接收角色，保留仍可用的手动选择，并纳入本次新安装的角色。"""
        targets = read_targets(PROJECT_ROOT)
        receiver = self.characters.receiver
        previous = receiver.currentData()
        receiver.blockSignals(True)
        receiver.clear()
        receiver.addItem("请选择本地角色", None)
        for target in targets:
            if target.usable:
                receiver.addItem(f"{target.name}（{target.identifier}）", (target.identifier, target.name))
                if previous and previous[0] == target.identifier:
                    receiver.setCurrentIndex(receiver.count() - 1)
        receiver.blockSignals(False)
        value = receiver.currentData() if self.selection.mode == "existing" else None
        self.selection = replace(self.selection, target_id=value[0] if value else "",
                                 target_name=value[1] if value else "")
        return targets

    def match_on_entry(self) -> None:
        """每次进入模型列表重置安装选择，仅依据本次线上来源匹配唯一接收角色。"""
        receiver = self.characters.receiver
        receiver.blockSignals(True)
        receiver.setCurrentIndex(0)
        receiver.blockSignals(False)
        self.choose_mode("new")
        targets = self.refresh_targets()
        target = default_target(self.selection.source, targets)
        if target is None:
            return
        self.choose_mode("existing")
        receiver.setCurrentIndex(receiver.find_target(target.identifier))
        # self.selection_notice.setText(f"本地已有该角色，已切换为添加服装。接收角色：{target.name}。")
        # self.selection_notice.show()
        # self.selection_notice_timer.start()

    def model_installed(self, choice: Selection) -> None:
        """新角色安装成功后原地转为给该角色添加服装，已有角色安装保留手动目标。"""
        self.refresh_targets()
        if choice.mode == "new":
            index = self.characters.receiver.find_target(choice.source)
            if index > 0:
                self.choose_mode("existing")
                self.characters.receiver.setCurrentIndex(index)

    def update_installation_controls(self) -> None:
        """仅在模型列表显示用途与接收角色设置，角色页只承担线上来源选择。"""
        model_list = (self.stack.currentWidget() is self.resources
                      and self.selection.mode in ("new", "existing"))
        self.model_options.setVisible(model_list)
        self.receiver_bar.setVisible(model_list and self.selection.mode == "existing")
        self.characters.receiver.setVisible(model_list and self.selection.mode == "existing")
        if not model_list:
            self.selection_notice_timer.stop()
            self.selection_notice.hide()

    def request_receiver(self, targets: Sequence[LocalTarget]) -> bool:
        """在当前模型页补全接收角色，确认后继续刚才的下载，取消则留在原处。"""
        dialog = QDialog(self)
        dialog.setWindowTitle("选择本地接收角色")
        layout = QVBoxLayout(dialog)
        label = QLabel("这套服装要添加到哪个本地角色？", dialog)
        layout.addWidget(label)
        receiver = CharacterComboBox(dialog)
        receiver.addItem("请选择本地角色", None)
        for target in targets:
            if target.usable:
                receiver.addItem(f"{target.name}（{target.identifier}）", (target.identifier, target.name))
        selected = self.characters.receiver.currentData()
        recommended = default_target(self.selection.source, targets)
        if selected is None and recommended is not None:
            selected = (recommended.identifier, recommended.name)
        if selected is not None:
            receiver.setCurrentIndex(max(0, receiver.find_target(selected[0])))
        layout.addWidget(receiver)
        if receiver.count() == 1:
            label.setText("未找到可用的本地角色。已有目录可能不完整，请检查角色目录后重试。")
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel, parent=dialog)
        confirm = buttons.button(QDialogButtonBox.Ok)
        confirm.setText("添加并下载")
        buttons.button(QDialogButtonBox.Cancel).setText("取消")
        confirm.setEnabled(receiver.currentData() is not None)
        receiver.currentIndexChanged.connect(lambda _: confirm.setEnabled(receiver.currentData() is not None))
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        if dialog.exec_() != QDialog.Accepted:
            return False
        value = receiver.currentData()
        self.refresh_targets()
        index = self.characters.receiver.find_target(value[0]) if value else -1
        if index <= 0:
            QMessageBox.warning(self, "无法安装", "所选本地角色已发生变化，请重新选择。")
            return False
        self.characters.receiver.setCurrentIndex(index)
        return True

    def prepare_model_download(self) -> Optional[Selection]:
        """下载前重新验证磁盘状态，在原地处理新建冲突或补全接收角色。"""
        targets = self.refresh_targets()
        conflict = self.selection.mode == "new" and creation_conflicts(PROJECT_ROOT, self.selection.source, targets)
        if conflict:
            self.choose_mode("existing")
        if self.selection.mode == "existing" and (conflict or not self.selection.target_id):
            if not self.request_receiver(targets):
                self.resources.update_target()
                return None
        self.resources.update_target()
        return self.selection

    def update_navigation(self, busy: bool) -> None:
        """下载未结束时锁定顶部类型与用途，防止切换任务上下文。"""
        self.navigation.setEnabled(not busy)
        self.receiver_bar.setEnabled(not busy)

    def choose_mode(self, mode: str) -> None:
        """切换模型用途时原地更新接收角色，切换资源类型时进入对应页面。"""
        if self.hub.busy:
            return
        model_switch = self.selection.mode in ("new", "existing") and mode in ("new", "existing")
        if not model_switch:
            self.resources.generation += 1
            self.hub.cancel_reads()
            self.characters.receiver.blockSignals(True)
            self.characters.receiver.setCurrentIndex(0)
            self.characters.receiver.blockSignals(False)
            if mode in ("new", "existing"):
                mode = "new"
        if mode in ("new","existing"):
            self.model_mode = mode
        kind = "model" if mode in ("new","existing") else mode
        self.type_tabs[kind].setChecked(True)
        self.mode_tabs[self.model_mode].setChecked(True)
        source = (self.selection.source if model_switch else
                  self.characters.carousel.members[self.characters.carousel.index])
        target = self.characters.receiver.currentData() if mode == "existing" else None
        self.selection = Selection(mode,source,target[0] if target else "",target[1] if target else "")
        self.selection_notice_timer.stop()
        self.selection_notice.clear()
        self.selection_notice.hide()
        self.characters.update_next()
        if model_switch:
            self.resources.update_target()
        elif mode == "background":
            self.show_resources()
        else:
            self.stack.setCurrentWidget(self.characters)

        self.update_installation_controls()
        self.update()

    def show_home(self) -> None:
        """兼容旧返回入口，重定向至当前模型用途的角色选择页。"""
        if not self.hub.busy:
            self.resources.go_back()
            self.choose_mode(self.model_mode)

    def show_resources(self) -> None:
        """进入资源页并按当前不可变选择查询目录。"""
        if self.hub.busy:
            return
        if self.stack.currentWidget() is self.characters and self.selection.mode in ("new", "existing"):
            self.match_on_entry()
        self.stack.setCurrentWidget(self.resources)
        self.resources.load()

    def reject(self):
        """让 Escape 关闭操作也遵守取消与等待后台退出的规则。"""
        self.close()  # Escape 也走相同的取消和等待流程。

    def closeEvent(self, event):
        """有后台任务时先取消并异步等待，避免销毁仍被使用的控件。"""
        if self.hub.idle():
            self.close_timer.stop()
            session = self.zip_root.resolve()
            if session.parent == (CACHE_ROOT / "resource-downloads").resolve() and not self.zip_root.is_symlink():
                try:
                    session.rmdir()  # 只删除已清空的本次会话目录。
                except OSError:
                    pass
            event.accept()
            return
        event.ignore()
        self.closing = True
        self.hub.cancel_all()
        self.setWindowTitle("正在停止任务，请稍候…")
        self.stack.setEnabled(False)
        self.close_timer.start()

    def finish_close(self):
        """后台完全退出后再次关闭窗口，结束定时等待。"""
        if self.hub.idle():
            self.close_timer.stop()
            self.close()
