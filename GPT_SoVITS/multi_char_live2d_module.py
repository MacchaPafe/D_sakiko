from __future__ import annotations

import contextlib
from live2d.utils.lipsync import WavHandler
from OpenGL.GL import *
import glob, os,time
from random import randint
import sys
from collections import deque
from typing import Dict, List, Any, Optional
from types import ModuleType

script_dir = os.path.dirname(os.path.abspath(__file__))
if script_dir not in sys.path:
    sys.path.insert(0, script_dir)

with open(os.devnull, 'w') as devnull:
    with contextlib.redirect_stdout(devnull):
        with contextlib.redirect_stderr(devnull):
            import pygame
            from pygame.locals import DOUBLEBUF, OPENGL


from log import get_logger
from live2d_support.layout import (
    Live2DLayout,
    format_live2d_layout_status,
    get_live2d_layout,
    reset_live2d_layout,
    save_live2d_layout,
)
from live2d_support.runtime_adapter import (
    Live2DModelAdapter,
    MotionPosition,
    Live2DVersion,
    detect_live2d_runtime_version,
    initialize_live2d_runtime,
    load_live2d_runtime,
    release_live2d_runtime,
)

logger = get_logger(__name__)


def _load_cjk_font(size: int, bold: bool = False) -> pygame.font.Font:
    """尽量加载支持中日韩(CJK)字符的字体。

    优先：项目内置 font/msyh.ttc
    其次：在系统字体里按候选列表匹配（Windows/macOS/Linux）

    说明：macOS 上原先写死的 "Microsoft YaHei" 往往不存在，
    pygame 会回退到不含中文的默认字体，从而显示为“方块”。
    """

    if not pygame.font.get_init():
        pygame.font.init()

    # 1) 项目内置字体（相对仓库根目录）
    script_dir = os.path.dirname(os.path.abspath(__file__))
    project_root = os.path.abspath(os.path.join(script_dir, ".."))
    bundled_font_path = os.path.join(project_root, "font", "msyh.ttc")
    if os.path.exists(bundled_font_path):
        font = pygame.font.Font(bundled_font_path, size)
        font.set_bold(bold)
        return font

    # 2) 系统字体候选（尽量覆盖 Win/macOS/Linux）
    candidates = [
        # Windows
        "Microsoft YaHei",
        "Microsoft YaHei UI",
        "SimHei",
        "SimSun",
        "MS Gothic",
        # macOS
        "PingFang SC",
        "Hiragino Sans GB",
        "Heiti SC",
        "STHeiti",
        # 常见跨平台/发行版字体
        "Noto Sans CJK SC",
        "Noto Sans SC",
        "WenQuanYi Micro Hei",
        "Arial Unicode MS",
    ]

    for name in candidates:
        try:
            matched_path = pygame.font.match_font(name)
        except Exception:
            matched_path = None
        if matched_path:
            font = pygame.font.Font(matched_path, size)
            font.set_bold(bold)
            return font

    # 3) 最后兜底：默认字体（可能不含中文，但避免崩溃）
    font = pygame.font.Font(None, size)
    font.set_bold(bold)
    return font


class TextOverlay:
    def __init__(self, window_size:tuple,char_names:list):
        self.win_w, self.win_h = window_size
        self.texture_id = glGenTextures(1)

        # === 1. 动态计算画布尺寸 (基于窗口大小) ===
        self.surface_w = int(self.win_w * 0.8)  # 宽度占窗口 90%
        self.surface_h = int(self.win_h * 0.15)  # 高度占窗口 35%

        self.rect_surface = pygame.Surface((self.surface_w, self.surface_h), pygame.SRCALPHA)

        # === 2. 动态计算字体大小 (基于画布高度) ===
        # 设定字号为画布高度的 14% (主文本) 和 12% (名字)
        # max(12, ...) 是为了防止窗口太小时字号变为 0 或太小看不清
        self.font_size_main = max(12, int(self.surface_h * 0.14))
        self.font_size_name = max(10, int(self.surface_h * 0.15))

        # macOS 下常见问题：找不到“微软雅黑”导致回退字体不含中文，显示成方块
        self.main_font = _load_cjk_font(self.font_size_main, bold=True)
        self.name_font = _load_cjk_font(self.font_size_name, bold=True)

        # === 3. 动态计算 UI 元素的尺寸参数 ===
        # 名字框高度：占画布高度的 22%
        self.ui_name_box_h = int(self.surface_h * 0.22)
        # 名字框圆角
        self.ui_name_radius = self.ui_name_box_h // 2

        # 主框圆角
        self.ui_main_radius = int(self.surface_h * 0.15)
        # 边框粗细：至少 2 像素
        self.ui_border_thick = max(3, int(self.surface_h * 0.015))

        # 文本边距
        self.ui_padding_x = int(self.surface_w * 0.02)

        # === 颜色定义 ===
        self.COLOR_WHITE_BG = (255, 255, 255, 220)
        self.COLOR_BLACK_BORDER = (0, 0, 0, 48)
        self.COLOR_PINK_BG = (255, 59, 113, 255)
        self.COLOR_TEXT_MAIN = (60, 60, 60)
        self.COLOR_TEXT_NAME = (255, 255, 255)

        # === OpenGL 坐标 ===
        bottom_y = -0.95
        # 动态计算上边界：根据 surface_h 在 win_h 中的占比推算
        # OpenGL 坐标系高度是 2.0 (-1 到 1)
        top_y = bottom_y + (self.surface_h / self.win_h) * 2 * 0.95

        self.quad_vertices = [
            -0.8, top_y, 0.0,
            0.8, top_y, 0.0,
            0.8, bottom_y, 0.0,
            -0.8, bottom_y, 0.0
        ]

        # === 【新增】打字机效果所需的状态变量 ===
        self.full_text = ""  # 完整的目标文本
        self.current_text = ""  # 当前屏幕上显示的文本
        self.char_pointer = 0.0  # 当前显示到的字符索引 (用浮点数以支持非整数速度)
        self.typing_speed = 2  # 打字速度：每帧显示的字符数
        self.current_char_name = ""  # 当前角色名 (用于重绘)
        self.is_typing = False  # 是否正在打字中

        try:
            raw_star = pygame.image.load('./icons/star.png').convert_alpha()
            # 动态缩放：让星星的大小等于主字体的大小 (或者稍微大一点，比如 1.2倍)
            star_size = int(self.font_size_main * 1.0)
            self.star_img = pygame.transform.smoothscale(raw_star, (star_size, star_size))
        except Exception:
            logger.exception("星星图标加载失败")
            self.star_img = None  # 标记为 None，防止后面报错
        if len(char_names)==1:
            self.set_text(char_names[0], "...")
        else:
            self.set_text(f"{char_names[0]} & {char_names[1]}", "...")

    def set_text(self, char_name, text):
        """
        【功能变更】这里只负责设置目标，不再直接渲染
        当外部队列传来新的一句话时调用此方法
        """
        # 如果文本没变，就不重置（防止重复调用导致闪烁）
        if text == self.full_text and char_name == self.current_char_name:
            return

        self.current_char_name = char_name
        self.full_text = text
        self.char_pointer = 0.0  # 重置指针
        self.current_text = ""  # 清空当前显示
        self.is_typing = True  # 开始打字状态

        # 立即渲染第一帧（空的或者只有名字），避免黑屏
        self._render_texture()

    def update(self):
        """
        【新增】需要在 Pygame 主循环中每帧调用
        负责计算当前应该显示多少个字
        """
        if not self.is_typing:
            return

        # 1. 增加指针
        if self.char_pointer < len(self.full_text):
            self.char_pointer += self.typing_speed

            # 2. 截取当前应该显示的文本
            # int() 向下取整，保证索引是整数
            display_count = int(self.char_pointer)

            # 防越界处理
            if display_count > len(self.full_text):
                display_count = len(self.full_text)

            new_slice = self.full_text[:display_count]

            # 3. 只有当显示的文字真的变多了，才重新生成纹理 (节省性能)
            if new_slice != self.current_text:
                self.current_text = new_slice
                self._render_texture()  # 调用绘图逻辑

            # 检查是否打完了
            if display_count >= len(self.full_text):
                self.is_typing = False
        else:
            self.is_typing = False

    def _render_texture(self):
        char_name = self.current_char_name
        text = self.current_text  # <--- 关键：画的是截取后的部分文本
        self.rect_surface.fill((0, 0, 0, 0))

        # === 使用 __init__ 里计算好的动态参数 ===

        # 1. 布局定位
        # 主框向下偏移，给名字框留出位置（正好等于名字框高度）
        main_box_offset_y = self.ui_name_box_h

        main_rect = pygame.Rect(
            self.ui_border_thick // 2,
            main_box_offset_y,
            self.surface_w - self.ui_border_thick,
            self.surface_h - main_box_offset_y - self.ui_border_thick // 2
        )

        # 2. 绘制主对话框
        pygame.draw.rect(self.rect_surface, self.COLOR_WHITE_BG, main_rect, border_radius=self.ui_main_radius)
        pygame.draw.rect(self.rect_surface, self.COLOR_BLACK_BORDER, main_rect, width=self.ui_border_thick,
                         border_radius=self.ui_main_radius)

        # 3. 绘制名字框
        name_w, _ = self.name_font.size(char_name)
        # 名字框宽度 = 文字宽 + 左右各 0.8倍字号的 padding
        name_box_width = int(self.surface_w * 0.15)
        # 位置：紧贴主框上沿 (main_box_offset_y - self.ui_name_box_h = 0)
        # x坐标：左边距
        # name_rect_x = int(self.surface_w * 0.05)
        name_rect = pygame.Rect(0, 0, name_box_width, self.ui_name_box_h)
        if char_name:
            pygame.draw.rect(self.rect_surface, self.COLOR_PINK_BG, name_rect, border_radius=self.ui_name_radius)
            # 名字框的白描边
            pygame.draw.rect(self.rect_surface, (255, 255, 255, 255), name_rect,
                             width=max(2, self.ui_border_thick // 2), border_radius=self.ui_name_radius)

            name_txt_img = self.name_font.render(char_name, True, self.COLOR_TEXT_NAME)
            name_txt_offset_x = name_rect.center[0]-name_box_width/2+self.font_size_name*len(char_name)/2
            name_txt_rect = name_txt_img.get_rect(center=name_rect.center)
            self.rect_surface.blit(name_txt_img, name_txt_rect)

        # 4. 绘制文本
        # 文本起始Y：主框偏移 + 名字框高度的一半作为 Padding
        text_start_y = main_box_offset_y + int(self.ui_name_box_h * 0.75)

        max_text_width = self.surface_w - (self.ui_padding_x * 2)
        lines = self.wrap_text(text, self.main_font, max_text_width)

        y_offset = text_start_y
        # 动态行距：字号的 1.5 倍
        line_spacing = int(self.font_size_main * 1.5)

        for line in lines:
            txt_img = self.main_font.render(line, True, self.COLOR_TEXT_MAIN)
            self.rect_surface.blit(txt_img, (self.ui_padding_x, y_offset))
            y_offset += line_spacing

        if self.star_img is not None:
            star_x = name_rect.center[0] + name_box_width / 2 - 2 * self.ui_name_radius
            star_y=name_rect.center[1]-self.ui_name_box_h/4
            #self.rect_surface.blit(self.star_img, (star_x, star_y))

        # 5. 生成纹理 (保持不变)
        texture_data = pygame.image.tostring(self.rect_surface, "RGBA", True)
        width, height = self.rect_surface.get_size()
        glBindTexture(GL_TEXTURE_2D, self.texture_id)
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_LINEAR)
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_LINEAR)
        glTexImage2D(GL_TEXTURE_2D, 0, GL_RGBA, width, height, 0, GL_RGBA, GL_UNSIGNED_BYTE, texture_data)
        glBindTexture(GL_TEXTURE_2D, 0)


    def draw(self):
        # 【关键修改2】保存之前的状态 (存档)
        # 这会保存深度测试、混合模式、剔除模式等所有状态
        glPushAttrib(GL_ALL_ATTRIB_BITS)

        # 【关键修改3】强制设置 UI 绘制所需的状态
        glUseProgram(0)  # 必加：切回固定管线，禁用Live2D shader
        # glBindBuffer(GL_ARRAY_BUFFER, 0)  # 解绑顶点缓冲区 !!!关键!!!
        # glBindBuffer(GL_ELEMENT_ARRAY_BUFFER, 0)  # 解绑索引缓冲区 !!!关键!!!
        # glBindVertexArray(0)  # 解绑 VAO (如果有)
        # glDisable(GL_DEPTH_TEST)  # 必加：关闭深度测试，让文字直接画在屏幕最前面
        # glDisable(GL_CULL_FACE)  # 必加：关闭面剔除，防止画反了看不见
        # glDisable(GL_LIGHTING)  # 关闭光照（如果有的话）
        # glMatrixMode(GL_PROJECTION)  # 切换到投影矩阵
        # glLoadIdentity()  # 重置为单位矩阵 (默认视口 -1 到 1)
        # glMatrixMode(GL_MODELVIEW)  # 切换到模型视图矩阵
        # glLoadIdentity()  # 重置
        # for i in range(5):
        #     glDisableVertexAttribArray(i)

            # 确保只使用 0号纹理单元
        glActiveTexture(GL_TEXTURE0)
        #

        glEnable(GL_BLEND)  # 开启混合
        glBlendFunc(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA)

        glEnable(GL_TEXTURE_2D)
        glBindTexture(GL_TEXTURE_2D, self.texture_id)

        # 重置绘制颜色为纯白，防止之前 Live2D 的颜色残留影响贴图颜色
        glColor4f(1, 1, 1, 1)

        glBegin(GL_QUADS)
        # 纹理坐标 (0,0) 通常对应左下，(0,1) 对应左上
        # 如果 tostring(..., True) 翻转了，这里的映射应该是直观的
        glTexCoord2f(0, 1)
        glVertex3f(self.quad_vertices[0], self.quad_vertices[1], self.quad_vertices[2])  # 左上
        glTexCoord2f(1, 1)
        glVertex3f(self.quad_vertices[3], self.quad_vertices[4], self.quad_vertices[5])  # 右上
        glTexCoord2f(1, 0)
        glVertex3f(self.quad_vertices[6], self.quad_vertices[7], self.quad_vertices[8])  # 右下
        glTexCoord2f(0, 0)
        glVertex3f(self.quad_vertices[9], self.quad_vertices[10], self.quad_vertices[11])  # 左下
        glEnd()

        # 【关键修改4】恢复之前的状态 (读档)
        glPopAttrib()

    def wrap_text(self, text, font,max_width):
        # ... (保持原来的换行代码不变) ...
        words = list(text)
        lines = []
        current_line = []
        for word in words:
            if word=='\n':
                lines.append(''.join(current_line))
                current_line = []
                continue
            test_line = ''.join(current_line + [word])
            w, h = font.size(test_line)
            if w < max_width:
                current_line.append(word)
            else:
                lines.append(''.join(current_line))
                current_line = [word]
        lines.append(''.join(current_line))
        return lines


class SlotVersionNoticeOverlay:
    """绘制小剧场版本混用时隐藏槽位的持续提示。"""

    def __init__(self, window_size: tuple[int, int]) -> None:
        """初始化半屏版本提示的字体、尺寸和纹理缓存。"""
        self.win_w, self.win_h = window_size
        self.surface_w = int(self.win_w * 0.42)
        self.surface_h = int(self.win_h * 0.13)
        self.title_font = _load_cjk_font(max(16, int(self.win_h * 0.035)), bold=True)
        self.detail_font = _load_cjk_font(max(12, int(self.win_h * 0.022)), bold=False)
        self.texture_ids: dict[int, object] = {}
        self.messages: dict[int, tuple[str, str]] = {}

    def clear(self) -> None:
        """清空所有隐藏槽位提示并释放纹理。"""
        self.dispose()
        self.messages.clear()

    def dispose(self) -> None:
        """释放提示纹理。"""
        for texture_id in self.texture_ids.values():
            try:
                glDeleteTextures([texture_id])
            except Exception:
                pass
        self.texture_ids.clear()

    def set_hidden_slots(
            self,
            hidden_slots: set[int],
            active_slots: list[dict[str, object]],
            versions: list[Live2DVersion],
    ) -> None:
        """根据隐藏槽位集合刷新半屏提示内容。"""
        next_messages: dict[int, tuple[str, str]] = {}
        for slot in hidden_slots:
            character_name = str(active_slots[slot].get("character_name", f"slot{slot}"))
            title = f"{character_name} 暂时隐藏"
            if slot == 0:
                detail = f"Live2D 版本不一致：\n当前角色 {versions[0]} / 另一角色 {versions[1]}，请切换为同版本模型"
            else:
                detail = f"Live2D 版本不一致：\n当前角色 {versions[1]} / 另一角色 {versions[0]}，请切换为同版本模型"
            next_messages[slot] = (title, detail)

        for slot in tuple(self.messages):
            if slot not in next_messages:
                self._delete_slot_texture(slot)

        for slot, message in next_messages.items():
            if self.messages.get(slot) != message:
                self.messages[slot] = message
                self._render_slot_texture(slot, message[0], message[1])

        self.messages = next_messages

    def _delete_slot_texture(self, slot: int) -> None:
        """删除指定槽位的提示纹理。"""
        texture_id = self.texture_ids.pop(slot, None)
        if texture_id is None:
            return
        try:
            glDeleteTextures([texture_id])
        except Exception:
            pass

    def _render_slot_texture(self, slot: int, title: str, detail: str) -> None:
        """把指定槽位的提示文字渲染成 OpenGL 纹理。"""
        surface = pygame.Surface((self.surface_w, self.surface_h), pygame.SRCALPHA)
        surface.fill((0, 0, 0, 0))
        rect = surface.get_rect()
        pygame.draw.rect(surface, (24, 28, 34, 188), rect, border_radius=max(8, self.surface_h // 8))
        pygame.draw.rect(surface, (255, 255, 255, 118), rect, width=2, border_radius=max(8, self.surface_h // 8))

        self._draw_centered_lines(
            surface,
            [title],
            self.title_font,
            (255, 255, 255),
            int(self.surface_h * 0.32),
        )
        self._draw_centered_lines(
            surface,
            detail.splitlines() or [detail],
            self.detail_font,
            (232, 238, 246),
            int(self.surface_h * 0.72),
        )

        texture_id = self.texture_ids.get(slot)
        if texture_id is None:
            texture_id = glGenTextures(1)
            self.texture_ids[slot] = texture_id

        texture_data = pygame.image.tostring(surface, "RGBA", True)
        glActiveTexture(GL_TEXTURE0)
        glBindTexture(GL_TEXTURE_2D, texture_id)
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_LINEAR)
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_LINEAR)
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_S, GL_CLAMP_TO_EDGE)
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_T, GL_CLAMP_TO_EDGE)
        glTexImage2D(GL_TEXTURE_2D, 0, GL_RGBA, self.surface_w, self.surface_h, 0, GL_RGBA, GL_UNSIGNED_BYTE, texture_data)
        glBindTexture(GL_TEXTURE_2D, 0)

    def _draw_centered_lines(
            self,
            surface: pygame.Surface,
            lines: list[str],
            font: pygame.font.Font,
            color: tuple[int, int, int],
            center_y: int,
    ) -> None:
        """在提示纹理上按换行逐行居中绘制文字。"""
        line_spacing = int(font.get_linesize() * 1.05)
        total_height = line_spacing * max(0, len(lines) - 1) + font.get_height()
        y = center_y - total_height // 2
        for line in lines:
            line_image = font.render(line, True, color)
            line_rect = line_image.get_rect(center=(self.surface_w // 2, y + font.get_height() // 2))
            surface.blit(line_image, line_rect)
            y += line_spacing

    def draw(self) -> None:
        """绘制隐藏半边遮罩和提示文字。"""
        if not self.messages:
            return

        glPushAttrib(GL_ALL_ATTRIB_BITS)
        glUseProgram(0)
        glActiveTexture(GL_TEXTURE0)
        glDisable(GL_DEPTH_TEST)
        glDisable(GL_CULL_FACE)
        glDisable(GL_SCISSOR_TEST)
        glDisable(GL_ALPHA_TEST)
        glEnable(GL_BLEND)
        glBlendFunc(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA)
        glDisable(GL_TEXTURE_2D)

        for slot in self.messages:
            left = -1.0 if slot == 0 else 0.0
            right = 0.0 if slot == 0 else 1.0
            glColor4f(0.0, 0.0, 0.0, 0.28)
            glBegin(GL_QUADS)
            glVertex3f(left, 1.0, 0.0)
            glVertex3f(right, 1.0, 0.0)
            glVertex3f(right, -1.0, 0.0)
            glVertex3f(left, -1.0, 0.0)
            glEnd()

        glEnable(GL_TEXTURE_2D)
        glTexEnvi(GL_TEXTURE_ENV, GL_TEXTURE_ENV_MODE, GL_MODULATE)
        glColor4f(1.0, 1.0, 1.0, 1.0)
        for slot in self.messages:
            texture_id = self.texture_ids.get(slot)
            if texture_id is None:
                continue
            center_x = -0.5 if slot == 0 else 0.5
            center_y = 0.38
            half_w = (self.surface_w / self.win_w)
            half_h = (self.surface_h / self.win_h)
            left = center_x - half_w
            right = center_x + half_w
            top = center_y + half_h
            bottom = center_y - half_h

            glBindTexture(GL_TEXTURE_2D, texture_id)
            glBegin(GL_QUADS)
            glTexCoord2f(0, 1)
            glVertex3f(left, top, 0.0)
            glTexCoord2f(1, 1)
            glVertex3f(right, top, 0.0)
            glTexCoord2f(1, 0)
            glVertex3f(right, bottom, 0.0)
            glTexCoord2f(0, 0)
            glVertex3f(left, bottom, 0.0)
            glEnd()
        glBindTexture(GL_TEXTURE_2D, 0)
        glPopAttrib()


class BackgroundRen(object):

    @staticmethod
    def render(surface: pygame.SurfaceType) -> object:
        texture_data = pygame.image.tostring(surface, "RGBA", True)
        id: object = glGenTextures(1)
        glBindTexture(GL_TEXTURE_2D, id)
        glTexImage2D(
            GL_TEXTURE_2D,
            0,
            GL_RGBA,
            surface.get_width(),
            surface.get_height(),
            0,
            GL_RGBA,
            GL_UNSIGNED_BYTE,
            texture_data
        )
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_LINEAR)
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_LINEAR)
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_S, GL_CLAMP_TO_EDGE)
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_T, GL_CLAMP_TO_EDGE)
        return id

    @staticmethod
    def blit(*args: tuple[tuple[int, int, int], tuple[int, int, int], tuple[int, int, int], tuple[int, int, int]]) -> None:
        glBegin(GL_QUADS)
        glTexCoord2f(0, 0)
        glVertex3f(*args[3])
        glTexCoord2f(1, 0)
        glVertex3f(*args[1])
        glTexCoord2f(1, 1)
        glVertex3f(*args[2])
        glTexCoord2f(0, 1)
        glVertex3f(*args[0])
        glEnd()


idle_recover_timer = time.time()


class Live2DModule:
    def __init__(self,characters):
        self.BACK_IMAGE = None
        self.BACKGROUND_POSITION = ((-1.0, 1.0, 0), (1.0, -1.0, 0), (1.0, 1.0, 0), (-1.0, -1.0, 0))
        self.run = True
        # self.sakiko_state = True
        # self.if_sakiko = False
        # self.if_mask = True
        self.character_list = []
        # 当前渲染的两个对话角色
        # 每个元素是一个 dict，包含 "slot"（0 或 1，对应左侧/右侧渲染的角色）、"character_name"（字符串）和 "model_json_path"（字符串）三个键
        self.active_slots: List[Dict[str, Any]] = []
        self.live2D_initialize(characters)
        self.model_pos_offset=[(-0.4,0),(0.4,0)]  #两个模型的位置偏移设置
        self.model_scale = 0.8
        self.wavHandler = WavHandler()
        self.lipSyncN: float = 1.4
        self.motion_is_over=True
        self.last_motion_model = None
        self.force_eyes_open = False
        self.motion_finished_at = 0.0

        # 所有需要播放的对话内容组成的列表
        self.playlist=[]
        # 当前播放到播放列表的哪个位置
        self.playlist_pointer=0

    def _start_turn_audio(self, audio_file_path: str) -> bool:
        """播放当前句音频并启动口型同步，返回是否存在有效音频。"""
        self.motion_is_over = False
        if not audio_file_path or not os.path.exists(audio_file_path):
            self.motion_is_over = True
            return False

        try:
            pygame.mixer.music.load(audio_file_path)
            pygame.mixer.music.play()
        except Exception:
            self.motion_is_over = True
            return False

        if audio_file_path != '../reference_audio/silent_audio/silence.wav':
            try:
                self.wavHandler.Start(audio_file_path)
            except Exception:
                logger.debug("小剧场口型同步读取音频失败，已跳过：%s", audio_file_path, exc_info=True)
        return True

    def _try_start_emotion_motion(
            self,
            model: Live2DModelAdapter | None,
            emotion: str,
            position: MotionPosition,
    ) -> bool:
        """尝试根据情绪触发可见模型动作，动作缺失时安静失败。"""
        if model is None:
            self.last_motion_model = None
            self.force_eyes_open = False
            self.motion_is_over = True
            return False

        emotion_to_group = {
            "happiness": "happiness",
            "sadness": "sadness",
            "anger": "anger",
            "disgust": "disgust",
            "like": "like",
            "surprise": "surprise",
            "fear": "fear",
        }
        group_name = emotion_to_group.get(str(emotion).strip(), "IDLE")
        self.last_motion_model = model
        self.force_eyes_open = False

        started = model.StartRandomMotion(group_name, 3, None, self.onFinishCallback_motion, position=position)
        if not started:
            # logger.warning("Live2D 动作组不存在或启动失败，已跳过：%s", group_name)
            self.motion_is_over = True
            return False
        return True

    @staticmethod
    def _motion_position_for_slot(
            slot: int | None,
            versions: list[Live2DVersion],
            facing_mode: str,
    ) -> MotionPosition:
        """根据小剧场槽位、模型版本和朝向模式选择动作位置。"""
        if facing_mode == "face_to_face" and versions == ["v3", "v3"]:
            if slot == 0:
                return "L"
            if slot == 1:
                return "R"
        return "C"

    def _build_default_active_slots(self) -> List[Dict[str, Any]]:
        """从角色列表前两位构建默认槽位配置。

        槽位约定：
        - slot=0：左侧显示的模型
        - slot=1：右侧显示的模型
        """
        return [
            {
                "slot": 0,
                "character_name": self.character_list[0].character_name,
                "model_json_path": self.character_list[0].live2d_json,
            },
            {
                "slot": 1,
                "character_name": self.character_list[1].character_name,
                "model_json_path": self.character_list[1].live2d_json,
            }
        ]

    def _normalize_slots_payload(self, slots_payload: Any) -> List[Dict[str, Any]]:
        """校验并标准化 `set_active_slots` 的输入内容。

        设计目的：
        - 强制要求两个待更新模型信息（0/1）都存在，防止半更新状态。
        - 校验 `character_name` 与 `model_json_path` 的合法性。
        - 最终返回固定顺序（0,1）的槽位列表，便于后续直接索引。
        """
        if not isinstance(slots_payload, list) or len(slots_payload) != 2:
            raise ValueError("set_active_slots 需要提供两个槽位")

        normalized: List[Dict[str, Any]] = []
        for expected_slot in (0, 1):
            slot_data = None
            for item in slots_payload:
                # 允许前端传入任意顺序，这里按 slot 值找到对应项
                if isinstance(item, dict) and item.get("slot") == expected_slot:
                    slot_data = item
                    break

            if slot_data is None:
                raise ValueError(f"缺少 slot={expected_slot} 的配置")

            character_name = str(slot_data.get("character_name", "")).strip()
            model_json_path = str(slot_data.get("model_json_path", "")).strip()

            if not character_name:
                raise ValueError(f"slot={expected_slot} 缺少 character_name")
            if not model_json_path:
                raise ValueError(f"slot={expected_slot} 缺少 model_json_path")
            if not os.path.exists(model_json_path):
                raise FileNotFoundError(f"slot={expected_slot} 模型文件不存在: {model_json_path}")

            normalized.append({
                "slot": expected_slot,
                "character_name": character_name,
                "model_json_path": model_json_path,
            })

        return normalized

    def _apply_model_common_setup(
            self,
            model: Live2DModelAdapter,
            win_w_and_h: int,
            slot: Optional[int] = None,
            layout: Optional[Live2DLayout] = None,
    ) -> None:
        """应用模型加载后的通用初始化参数，包含缩放、自动眨眼、自动呼吸等。"""
        model.Resize(int(win_w_and_h * 1.33), win_w_and_h)
        model.SetAutoBlinkEnable(True)
        model.SetAutoBreathEnable(True)
        if slot in (0, 1):
            applied_layout = layout if layout is not None else Live2DLayout(
                scale=self.model_scale,
                offset_x=0.0,
                offset_y=0.0,
            )
            slot_offset_x, slot_offset_y = self.model_pos_offset[slot]
            model.SetOffset(slot_offset_x + applied_layout.offset_x, slot_offset_y + applied_layout.offset_y)
            model.SetScale(applied_layout.scale)

    def _draw_layout_edit_selection_mask(self, selected_slot: int) -> None:
        """在小剧场布局编辑时绘制当前选中槽位的半屏提示。"""
        selected_left = -1.0 if selected_slot == 0 else 0.0
        selected_right = 0.0 if selected_slot == 0 else 1.0
        masked_left = 0.0 if selected_slot == 0 else -1.0
        masked_right = 1.0 if selected_slot == 0 else 0.0

        glPushAttrib(GL_ALL_ATTRIB_BITS)
        glUseProgram(0)
        glDisable(GL_TEXTURE_2D)
        glEnable(GL_BLEND)
        glBlendFunc(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA)

        glColor4f(0.0, 0.0, 0.0, 0.18)
        glBegin(GL_QUADS)
        glVertex3f(masked_left, 1.0, 0.0)
        glVertex3f(masked_right, 1.0, 0.0)
        glVertex3f(masked_right, -1.0, 0.0)
        glVertex3f(masked_left, -1.0, 0.0)
        glEnd()

        glLineWidth(3.0)
        glColor4f(0.50, 0.70, 0.95, 0.90)
        glBegin(GL_LINE_LOOP)
        glVertex3f(selected_left, 1.0, 0.0)
        glVertex3f(selected_right, 1.0, 0.0)
        glVertex3f(selected_right, -1.0, 0.0)
        glVertex3f(selected_left, -1.0, 0.0)
        glEnd()

        glPopAttrib()

    def _force_model_eyes_open(self, model: Live2DModelAdapter) -> None:
        """只恢复眼睛开合参数，保留动作尾帧的身体和表情姿势。"""
        try:
            model.set_parameter_value("eye_l_open", 1.0)
            model.set_parameter_value("eye_r_open", 1.0)
        except Exception:
            logger.debug("Live2D 模型不支持标准眼睛开合参数，跳过眼睛恢复", exc_info=True)

    def _clear_eye_reopen_state(self):
        self.last_motion_model = None
        self.force_eyes_open = False

    def _active_character_names(self) -> List[str]:
        """返回当前两个正在渲染的角色名，供 Overlay 等 UI 展示使用。"""
        if len(self.active_slots) < 2:
            # 理论上不应该发生，除非 qt 那边传了错误数据导致初始化失败。这里做个兜底，避免崩溃。
            return ["角色A", "角色B"]
        return [self.active_slots[0]["character_name"], self.active_slots[1]["character_name"]]

    def _active_model_paths(self) -> list[str]:
        """返回当前两个槽位的目标模型路径。"""
        return [
            str(self.active_slots[0]["model_json_path"]),
            str(self.active_slots[1]["model_json_path"]),
        ]

    def _active_model_versions(self) -> list[Live2DVersion]:
        """检测当前两个槽位目标模型的 Live2D runtime 版本。"""
        return [detect_live2d_runtime_version(path) for path in self._active_model_paths()]

    def _select_runtime_version(self, versions: list[Live2DVersion], changed_slot: int | None) -> Live2DVersion:
        """根据版本列表和本次变更槽位选择当前窗口 runtime 版本。"""
        if versions[0] == versions[1]:
            return versions[0]
        selected_slot = changed_slot if changed_slot in (0, 1) else 0
        return versions[selected_slot]

    def _visible_slots_for_runtime(
            self,
            versions: list[Live2DVersion],
            runtime_version: Live2DVersion,
            changed_slot: int | None,
    ) -> set[int]:
        """计算当前 runtime 下应当渲染的槽位集合。"""
        if versions[0] == versions[1]:
            return {0, 1}
        selected_slot = changed_slot if changed_slot in (0, 1) else 0
        logger.warning(
            "小剧场 Live2D 模型版本不一致，暂时只显示一个角色：%s / %s",
            selected_slot,
            versions[0],
            versions[1],
        )
        if versions[selected_slot] != runtime_version:
            return {slot for slot, version in enumerate(versions) if version == runtime_version}
        return {selected_slot}

    def _dispose_model_group(self, model_group: list[Live2DModelAdapter | None]) -> None:
        """释放当前两个渲染槽位中的模型。"""
        for index, model in enumerate(model_group):
            if model is not None:
                try:
                    model.dispose()
                except Exception:
                    logger.debug("释放小剧场 Live2D 模型失败", exc_info=True)
            model_group[index] = None

    def _load_visible_models(
            self,
            model_group: list[Live2DModelAdapter | None],
            model_layouts: list[Live2DLayout],
            win_w_and_h: int,
            changed_slot: int | None,
            runtime_version: Live2DVersion,
    ) -> None:
        """按当前 runtime 加载可见槽位模型，并隐藏版本不一致的槽位。"""
        versions = self._active_model_versions()
        visible_slots = self._visible_slots_for_runtime(versions, runtime_version, changed_slot)
        self._dispose_model_group(model_group)

        for slot in (0, 1):
            if slot not in visible_slots:
                continue
            model_path = str(self.active_slots[slot]["model_json_path"])
            model = Live2DModelAdapter.create(model_path)
            model_layouts[slot] = get_live2d_layout(model_path, model.version, "theater")
            self._apply_model_common_setup(model, win_w_and_h, slot, model_layouts[slot])
            model_group[slot] = model

    def _handle_toggle_sakiko_model(
            self,
            model_group: list[Live2DModelAdapter | None],
            win_w_and_h: int,
            model_layouts: list[Live2DLayout],
            runtime_version: Live2DVersion,
    ) -> None:
        """在当前槽位中查找“祥子”，并切换黑/白祥模型。

        切换后会同步更新 `active_slots` 里的 `model_json_path`，保证状态与实际渲染一致。
        """
        sakiko_slot = None
        for slot_data in self.active_slots:
            if slot_data.get("character_name") == "祥子":
                sakiko_slot = int(slot_data.get("slot", -1))
                break

        if sakiko_slot not in (0, 1):
            logger.warning("切换祥子模型失败：当前对话角色中没有‘祥子’")
            return

        this_sakiko_model = model_group[sakiko_slot]
        if runtime_version != "v2" or this_sakiko_model is None:
            logger.warning("当前小剧场 Live2D runtime 不是 v2 或祥子模型不可见，跳过祥子特殊模型切换。")
            return

        raw_model = this_sakiko_model.model
        model_home_dir = str(getattr(raw_model, "modelHomeDir", ""))
        if "live2D_model_costume" in model_home_dir:
            new_model_path = '../live2d_related/sakiko/live2D_model/3.model.json'
        else:
            new_model_path = '../live2d_related/sakiko/live2D_model_costume/3.model.json'

        model_group[sakiko_slot].dispose()
        model_group[sakiko_slot] = Live2DModelAdapter.create(new_model_path)
        model_layouts[sakiko_slot] = get_live2d_layout(new_model_path, "v2", "theater")
        self._apply_model_common_setup(model_group[sakiko_slot], win_w_and_h, sakiko_slot, model_layouts[sakiko_slot])
        if "live2D_model_costume" in new_model_path:
            model_group[sakiko_slot].StartRandomMotion('IDLE', 3, position="C")
        self._clear_eye_reopen_state()
        self.active_slots[sakiko_slot]["model_json_path"] = new_model_path

    def live2D_initialize(self, characters):
        if len(characters)<2:
            raise ValueError("至少需要两个角色...")
        self.character_list = characters
        self.active_slots = self._build_default_active_slots()

        back_img_png = glob.glob(os.path.join("../live2d_related", f"*.png"))
        back_img_jpg = glob.glob(os.path.join("../live2d_related", f"*.jpg"))
        if not (back_img_png + back_img_jpg):
            raise FileNotFoundError("没有找到背景图片文件(.png/.jpg)，自带的也被删了吗...")
        self.BACK_IMAGE = max((back_img_jpg + back_img_png), key=os.path.getmtime)
        # print("Live2D初始化...OK")

    def onStartCallback_emotion_version(self,audio_file_path):
        self.motion_is_over=False
        # 播放音频。
        # 这里只负责“尝试播放当前句音频”，不负责“何时播放下一句”。
        # 下一句的调度时机统一由 play_live2d 主循环中的状态机控制。

        if not audio_file_path or not os.path.exists(audio_file_path):
            return

        try:
            pygame.mixer.music.load(audio_file_path)
            pygame.mixer.music.play()
        except Exception:
            return
        # 处理口型同步
        if audio_file_path!='../reference_audio/silent_audio/silence.wav':  #该函数无法处理无声音频
            self.wavHandler.Start(audio_file_path)

    def onFinishCallback_motion(self, *args):
        self.motion_is_over=True
        self.force_eyes_open = False
        self.motion_finished_at = time.time()

    def play_live2d(self,
                    change_char_queue,
                    to_live2d_module_queue,
                    tell_qt_this_turn_finish_queue):

        # print("正在开启Live2D模块")
        # 只在 Windows 下处理高DPI问题，因为 MacOS 下 PyQt 根本就没有模糊问题
        # 还有获得窗口位置的 api 是 Windows 特有的，MacOS 不能用
        if sys.platform == "win32":
            import ctypes

            # 1. 【关键】开启高DPI感知，解决模糊问题
            try:
                ctypes.windll.shcore.SetProcessDpiAwareness(1)
            except Exception:
                ctypes.windll.user32.SetProcessDPIAware()

            # 2. 直接用 ctypes 获取真实分辨率，不再需要 tkinter
            user32 = ctypes.windll.user32
            desktop_w = user32.GetSystemMetrics(0)
            desktop_h = user32.GetSystemMetrics(1)
            win_w_and_h = int(0.7 * desktop_h)  # 根据显示器分辨率定义窗口大小，保证每个人看到的效果相同
            pygame_win_pos_w, pygame_win_pos_h = int(0.55 * desktop_w - 1.33*win_w_and_h), int(
                0.5 * desktop_h - 0.5 * win_w_and_h)
            # 以上设置后，会差出一个恶心的标题栏高度，因此还要加上一个标题栏高度
            caption_height = (ctypes.windll.user32.GetSystemMetrics(4)
                              + ctypes.windll.user32.GetSystemMetrics(33)
                              + ctypes.windll.user32.GetSystemMetrics(92))  # 标题栏高度+厚度
            os.environ['SDL_VIDEO_WINDOW_POS'] = f"{pygame_win_pos_w},{pygame_win_pos_h + caption_height}"  # 设置窗口位置，与qt窗口对齐
        else:
            # MacOS / Linux 适配方案
            # 提前初始化 display 模块以获取屏幕信息
            if not pygame.display.get_init():
                pygame.display.init()
            
            info = pygame.display.Info()
            desktop_w = info.current_w
            desktop_h = info.current_h
            
            # 保持与 Windows 相同的窗口大小比例 (屏幕高度的 70%)
            win_w_and_h = int(0.7 * desktop_h)
            
            # 计算窗口位置 (保持相对位置逻辑一致)
            pygame_win_pos_w = int(0.55 * desktop_w - 1.33 * win_w_and_h)
            pygame_win_pos_h = int(0.5 * desktop_h - 0.5 * win_w_and_h)
            
            # 设置窗口位置环境变量
            # MacOS/Linux 不需要像 Windows 那样复杂的标题栏高度补偿，或者难以精确获取，直接使用计算出的位置
            os.environ['SDL_VIDEO_WINDOW_POS'] = f"{pygame_win_pos_w},{pygame_win_pos_h}"

        sdl_window_pos = os.environ.get('SDL_VIDEO_WINDOW_POS')
        pygame.init()

        display = (int(win_w_and_h*1.33), win_w_and_h)
        pygame.display.set_mode(display, DOUBLEBUF | OPENGL)
        current_runtime_version = self._select_runtime_version(self._active_model_versions(), None)
        current_runtime: ModuleType | None = load_live2d_runtime(current_runtime_version)
        initialize_live2d_runtime(current_runtime)
        frame_clock = pygame.time.Clock()
        target_fps = 60
        pygame.display.set_icon(pygame.image.load("../live2d_related/sakiko/sakiko_icon.png"))
        window_caption = "数字小祥 小剧场"
        pygame.display.set_caption(window_caption)
        model_layouts = [
            get_live2d_layout(
                self.active_slots[0]["model_json_path"],
                detect_live2d_runtime_version(self.active_slots[0]["model_json_path"]),
                "theater",
            ),
            get_live2d_layout(
                self.active_slots[1]["model_json_path"],
                detect_live2d_runtime_version(self.active_slots[1]["model_json_path"]),
                "theater",
            ),
        ]
        model_group: list[Live2DModelAdapter | None] = [None, None]
        self._load_visible_models(model_group, model_layouts, win_w_and_h, None, current_runtime_version)

        overlay = TextOverlay((int(win_w_and_h*1.33), win_w_and_h), self._active_character_names())
        version_notice_overlay = SlotVersionNoticeOverlay(display)
        # if self.if_sakiko:
        #     model.SetExpression('serious')
        glEnable(GL_TEXTURE_2D)

        # texture_thinking=BackgroundRen.render(pygame.image.load('X:\\D_Sakiko2.0\\live2d_related\\costumeBG.png').convert_alpha())    #想做背景切换功能，但无论如何都会有bug
        texture = BackgroundRen.render(pygame.image.load(self.BACK_IMAGE).convert_alpha())
        glBindTexture(GL_TEXTURE_2D, texture)

        global idle_recover_timer

        logger.info("当前 Live2D 界面渲染硬件：%s", glGetString(GL_RENDERER).decode())
        this_turn_model: Live2DModelAdapter | None = None
        lip_sync_model: Live2DModelAdapter | None = None
        # ===== 句间等待策略（固定规则） =====
        # 1) 有音频：音频播放结束后，再等待 0.5 秒进入下一句
        # 2) 无音频：当前句展示后，每个字等待 0.1 秒，等所有字展示完后，再等待 1.5 秒进入下一句
        WAIT_SECONDS = 0.5
        WAIT_AFTER_NO_AUDIO_RATIO = 0.1

        # 下一句最早可开始的绝对时间戳（time.time()）
        next_turn_earliest_start_at = 0.0
        # 当前是否处于“句间冷却”阶段
        waiting_between_turns = False
        # 上一帧是否检测到 pygame 音频正在播放
        previous_frame_audio_playing = False
        # 当前句是否有有效音频文件（用于判断“音频结束后冷却”逻辑）
        current_turn_has_audio = False

        stop_on_next_sentence = False  # 是否在下一句开始时停止（用于外部发送 STOP 命令的响应）
        queued_playlist: deque[dict] = deque()  # 用于存储外部发送的新播放列表，等当前播放完再切换
        last_speaker_slot = 1
        active_motion_facing_mode = "screen"
        layout_editing = False
        layout_dragging = False
        layout_selected_slot = 0
        layout_dirty_slots: set[int] = set()
        layout_last_mouse_pos: tuple[int, int] | None = None

        def slot_from_mouse_x(mouse_x: int) -> int:
            """根据鼠标横坐标判断正在编辑的小剧场槽位。"""
            return 0 if mouse_x < display[0] / 2 else 1

        def apply_slot_layout(slot: int) -> None:
            """将指定槽位当前布局应用到对应模型。"""
            model = model_group[slot]
            if model is not None:
                self._apply_model_common_setup(model, win_w_and_h, slot, model_layouts[slot])

        def switch_runtime_if_needed(target_version: Live2DVersion) -> None:
            """在目标版本变化时重建小剧场 OpenGL 窗口与 Live2D runtime。"""
            nonlocal current_runtime_version, current_runtime, texture, overlay, version_notice_overlay, frame_clock, this_turn_model, lip_sync_model
            if target_version == current_runtime_version:
                return

            try:
                pygame.mixer.music.stop()
            except Exception:
                pass
            self.wavHandler = WavHandler()
            this_turn_model = None
            lip_sync_model = None
            self._dispose_model_group(model_group)
            release_live2d_runtime(current_runtime)
            current_runtime = None
            try:
                glDeleteTextures([texture])
            except Exception:
                pass
            version_notice_overlay.dispose()

            pygame.display.quit()
            pygame.display.init()
            if sdl_window_pos:
                os.environ['SDL_VIDEO_WINDOW_POS'] = sdl_window_pos
            pygame.display.set_mode(display, DOUBLEBUF | OPENGL)
            current_runtime = load_live2d_runtime(target_version)
            initialize_live2d_runtime(current_runtime)
            current_runtime_version = target_version
            frame_clock = pygame.time.Clock()
            pygame.display.set_icon(pygame.image.load("../live2d_related/sakiko/sakiko_icon.png"))
            pygame.display.set_caption(window_caption)
            glEnable(GL_TEXTURE_2D)
            texture = BackgroundRen.render(pygame.image.load(self.BACK_IMAGE).convert_alpha())
            glBindTexture(GL_TEXTURE_2D, texture)
            overlay = TextOverlay((int(win_w_and_h*1.33), win_w_and_h), self._active_character_names())
            version_notice_overlay = SlotVersionNoticeOverlay(display)

        def hidden_slots_for_current_models() -> set[int]:
            """返回当前因版本不一致或未加载而隐藏的槽位。"""
            return {slot for slot, model in enumerate(model_group) if model is None}

        def refresh_version_notice_overlay() -> set[int]:
            """刷新半屏版本不一致提示，并返回当前隐藏槽位集合。"""
            hidden_slots = hidden_slots_for_current_models()
            if not hidden_slots:
                version_notice_overlay.clear()
                return hidden_slots
            version_notice_overlay.set_hidden_slots(hidden_slots, self.active_slots, self._active_model_versions())
            return hidden_slots

        def show_version_mismatch_overlay(hidden_slots: set[int]) -> None:
            """在底部字幕区域显示一次版本混用提示。"""
            if not hidden_slots:
                overlay.set_text(f"{self.active_slots[0]['character_name']} & {self.active_slots[1]['character_name']}", "...")
                return
            visible_names = [
                str(self.active_slots[slot].get("character_name", f"slot{slot}"))
                for slot, model in enumerate(model_group)
                if model is not None
            ]
            hidden_names = [
                str(self.active_slots[slot].get("character_name", f"slot{slot}"))
                for slot in sorted(hidden_slots)
            ]
            overlay.set_text(
                "版本不一致",
                f"当前只显示 {'、'.join(visible_names)}，{'、'.join(hidden_names)} 已暂时隐藏。\n请将两名角色切换为同一 Live2D 版本。"
            )

        def is_dialogue_idle() -> bool:
            """判断小剧场当前是否没有正在播放或等待推进的对话句。"""
            return (
                self.playlist_pointer == len(self.playlist)
                and not pygame.mixer.music.get_busy()
                and (not waiting_between_turns or time.time() >= next_turn_earliest_start_at)
            )

        def apply_idle_facing_motion_if_dialogue_idle() -> None:
            """空闲时立即把两个可见模型切到当前朝向对应的 IDLE 动作。"""
            if not is_dialogue_idle():
                return

            versions = self._active_model_versions()
            for slot, model in enumerate(model_group):
                if model is None:
                    continue
                position = self._motion_position_for_slot(slot, versions, active_motion_facing_mode)
                started = model.StartRandomMotion("IDLE", 3, position=position)
                if not started:
                    model.SetSemanticExpression("idle")

        def apply_slots_payload(payload: dict[str, object]) -> None:
            """应用 set_active_slots 消息，并按版本一致性决定可见模型。"""
            nonlocal active_motion_facing_mode
            old_slots = [dict(slot_data) for slot_data in self.active_slots]
            old_runtime_version = current_runtime_version
            old_motion_facing_mode = active_motion_facing_mode
            try:
                slots_payload = payload.get("slots")
                normalized_slots = self._normalize_slots_payload(slots_payload)
                facing_mode_value = payload.get("motion_facing_mode")
                active_motion_facing_mode = (
                    facing_mode_value
                    if isinstance(facing_mode_value, str) and facing_mode_value in ("screen", "face_to_face")
                    else "screen"
                )
                self.active_slots = normalized_slots
                changed_slot_value = payload.get("changed_slot")
                changed_slot = int(changed_slot_value) if type(changed_slot_value) is int and changed_slot_value in (0, 1) else None
                target_runtime_version = self._select_runtime_version(self._active_model_versions(), changed_slot)
                switch_runtime_if_needed(target_runtime_version)
                self._load_visible_models(model_group, model_layouts, win_w_and_h, changed_slot, current_runtime_version)
                self._clear_eye_reopen_state()
                show_version_mismatch_overlay(refresh_version_notice_overlay())
                apply_idle_facing_motion_if_dialogue_idle()
            except Exception:
                logger.exception("小剧场 Live2D 模型切换失败，尝试恢复旧模型。")
                self.active_slots = old_slots
                active_motion_facing_mode = old_motion_facing_mode
                try:
                    switch_runtime_if_needed(old_runtime_version)
                    self._load_visible_models(model_group, model_layouts, win_w_and_h, None, current_runtime_version)
                    show_version_mismatch_overlay(refresh_version_notice_overlay())
                    apply_idle_facing_motion_if_dialogue_idle()
                except Exception:
                    logger.exception("恢复旧小剧场 Live2D 模型失败。")

        def show_layout_edit_overlay() -> None:
            """刷新小剧场布局编辑模式下的提示文本。"""
            slot_data = self.active_slots[layout_selected_slot]
            character_name = str(slot_data.get("character_name", f"slot{layout_selected_slot}"))
            overlay.set_text(
                character_name,
                "布局编辑中：左键拖动，滚轮缩放，R重置，Esc/Q保存并退出"
            )

        def show_hidden_slot_overlay(slot: int) -> None:
            """提示用户当前槽位因版本不一致而暂时隐藏。"""
            slot_data = self.active_slots[slot]
            character_name = str(slot_data.get("character_name", f"slot{slot}"))
            overlay.set_text(character_name, "该模型当前因 Live2D 版本不一致而暂时隐藏。")

        def save_dirty_layouts() -> None:
            """保存所有已修改槽位对应模型的小剧场布局。"""
            for slot in tuple(layout_dirty_slots):
                try:
                    save_live2d_layout(str(self.active_slots[slot]["model_json_path"]), "theater", model_layouts[slot])
                except Exception:
                    logger.exception("保存小剧场 Live2D 布局配置失败")
            layout_dirty_slots.clear()

        def enter_layout_edit_mode(selected_slot: int) -> None:
            """进入小剧场 Live2D 布局编辑模式。"""
            nonlocal layout_editing, layout_dragging, layout_selected_slot, layout_last_mouse_pos
            if model_group[selected_slot] is None:
                show_hidden_slot_overlay(selected_slot)
                return
            layout_editing = True
            layout_dragging = False
            layout_selected_slot = selected_slot
            layout_last_mouse_pos = None
            pygame.display.set_caption(f"{window_caption} - 布局编辑中")
            show_layout_edit_overlay()

        def exit_layout_edit_mode() -> None:
            """退出小剧场 Live2D 布局编辑模式，并保存已修改布局。"""
            nonlocal layout_editing, layout_dragging, layout_last_mouse_pos
            save_dirty_layouts()
            layout_editing = False
            layout_dragging = False
            layout_last_mouse_pos = None
            pygame.display.set_caption(window_caption)
            overlay.set_text(f"{self.active_slots[0]['character_name']} & {self.active_slots[1]['character_name']}", "...")

        def reset_selected_layout() -> None:
            """重置当前选中模型的小剧场布局。"""
            if model_group[layout_selected_slot] is None:
                show_hidden_slot_overlay(layout_selected_slot)
                return
            try:
                reset_live2d_layout(self.active_slots[layout_selected_slot]["model_json_path"], "theater")
            except Exception:
                logger.exception("重置小剧场 Live2D 布局配置失败")
            model_layouts[layout_selected_slot] = get_live2d_layout(
                self.active_slots[layout_selected_slot]["model_json_path"],
                "v2",
                "theater",
            )
            apply_slot_layout(layout_selected_slot)
            layout_dirty_slots.discard(layout_selected_slot)
            show_layout_edit_overlay()

        show_version_mismatch_overlay(refresh_version_notice_overlay())

        while self.run:
            for event in pygame.event.get():  # 退出程序逻辑
                if event.type == pygame.QUIT:
                    if layout_editing:
                        exit_layout_edit_mode()
                    self.run = False
                    break
                elif event.type == pygame.KEYDOWN and layout_editing:
                    if event.key in (pygame.K_ESCAPE, pygame.K_q):
                        exit_layout_edit_mode()
                    elif event.key == pygame.K_r:
                        reset_selected_layout()
                elif event.type == pygame.MOUSEBUTTONDOWN:
                    if event.button == 3:
                        if layout_editing:
                            exit_layout_edit_mode()
                        else:
                            enter_layout_edit_mode(slot_from_mouse_x(event.pos[0]))
                    elif layout_editing and event.button == 1:
                        clicked_slot = slot_from_mouse_x(event.pos[0])
                        if model_group[clicked_slot] is None:
                            show_hidden_slot_overlay(clicked_slot)
                            continue
                        layout_selected_slot = clicked_slot
                        layout_dragging = True
                        layout_last_mouse_pos = event.pos
                        show_layout_edit_overlay()
                    elif layout_editing and event.button in (4, 5):
                        model_layouts[layout_selected_slot] = model_layouts[layout_selected_slot].zoomed(
                            1 if event.button == 4 else -1
                        )
                        apply_slot_layout(layout_selected_slot)
                        layout_dirty_slots.add(layout_selected_slot)
                        show_layout_edit_overlay()
                elif event.type == pygame.MOUSEBUTTONUP and layout_editing and event.button == 1:
                    layout_dragging = False
                    layout_last_mouse_pos = None
                elif event.type == pygame.MOUSEMOTION and layout_editing and layout_dragging:
                    if layout_last_mouse_pos is not None:
                        last_x, last_y = layout_last_mouse_pos
                        current_x, current_y = event.pos
                        model_layouts[layout_selected_slot] = model_layouts[layout_selected_slot].moved_by_pixels(
                            current_x - last_x,
                            current_y - last_y,
                            display[0],
                            display[1],
                        )
                        apply_slot_layout(layout_selected_slot)
                        layout_dirty_slots.add(layout_selected_slot)
                        show_layout_edit_overlay()
                    layout_last_mouse_pos = event.pos
                elif event.type == pygame.MOUSEWHEEL and layout_editing:
                    model_layouts[layout_selected_slot] = model_layouts[layout_selected_slot].zoomed(int(event.y))
                    apply_slot_layout(layout_selected_slot)
                    layout_dirty_slots.add(layout_selected_slot)
                    show_layout_edit_overlay()
                else:
                    pass

            if not change_char_queue.empty():
                x = change_char_queue.get()
                if x == "EXIT":
                    if layout_editing:
                        exit_layout_edit_mode()
                    self.run = False
                    tell_qt_this_turn_finish_queue.put('EXIT')
                    continue

                if not isinstance(x, dict):
                    logger.warning("忽略不支持的 change_char_queue 消息：%s", x)
                    continue

                message_type = x.get("type")

                # preserve_playback=True 的语义是“切换模型但保留当前播放列表”，False 则是“切换模型并停止当前播放列表（如果有）”
                # 目前在切换不同角色的模型时停止播放列表，在切换同角色的不同模型时保留播放列表
                should_stop_current_playlist = (
                    message_type in ("set_active_slots", "toggle_sakiko_model")
                    and not bool(x.get("preserve_playback", False))
                )
                if should_stop_current_playlist and self.playlist_pointer != len(self.playlist):
                    stop_on_next_sentence = True

                try:
                    # 统一按 type 分发，避免旧字符串协议带来的解析歧义
                    if message_type == "set_active_slots":
                        if layout_editing:
                            exit_layout_edit_mode()
                        apply_slots_payload(x)
                    elif message_type == "toggle_sakiko_model":
                        if layout_editing:
                            exit_layout_edit_mode()
                        self._handle_toggle_sakiko_model(
                            model_group,
                            win_w_and_h,
                            model_layouts,
                            current_runtime_version,
                        )
                    elif message_type == "switch_l2d_fps":
                        fps = int(x.get("fps"))
                        if fps in (30, 60, 120):
                            target_fps = fps
                            logger.info("已切换小剧场 Live2D 渲染帧率为 %d fps", target_fps)
                        else:
                            logger.warning("忽略不支持的小剧场 Live2D 帧率：%s", fps)
                    elif message_type == "toggle_l2d_layout_edit":
                        if layout_editing:
                            exit_layout_edit_mode()
                        else:
                            enter_layout_edit_mode(layout_selected_slot)
                    else:
                        logger.warning("忽略未知消息类型：%s", message_type)
                except Exception:
                    logger.exception("处理 change_char_queue 消息失败")
                continue

            if not to_live2d_module_queue.empty():
                data = to_live2d_module_queue.get()
                if data == "STOP":
                    stop_on_next_sentence = True
                else:
                    if not isinstance(data, dict):
                        logger.warning("忽略不支持的 to_live2d_module_queue 消息：%s", data)
                        continue
                    preserve_playback = bool(data.get("preserve_playback", False))
                    # 开启 preserve_playback 时，将传入内容添加到播放队列，等待当前播放完再切换
                    # 否则，在本句话播放完成后立刻切换到新内容，清空播放队列
                    if self.playlist_pointer==len(self.playlist):
                        self.playlist = data.get("playlist", [])
                        self.playlist_pointer = 0
                        waiting_between_turns = False
                        next_turn_earliest_start_at = 0.0
                        previous_frame_audio_playing = False
                        current_turn_has_audio = False
                    else:
                        if not preserve_playback:
                            stop_on_next_sentence = True  # 否则等当前播放完再切换
                            queued_playlist.clear()  # 清空队列，丢弃之前 preserve_playback=True 添加但未播放的内容
                        queued_playlist.append(data.get("playlist", []))

            audio_is_playing = pygame.mixer.music.get_busy()
            if self.playlist_pointer < len(self.playlist):
                now = time.time()

                # 检测“有音频的一句”从播放中 -> 已结束 的时刻。
                # 一旦检测到结束，启动 0.5 秒冷却，防止句子衔接过快。
                if previous_frame_audio_playing and (not audio_is_playing) and current_turn_has_audio:
                    next_turn_earliest_start_at = now + WAIT_SECONDS
                    waiting_between_turns = True

                # 记录本帧状态，供下一帧做“边沿检测”。
                previous_frame_audio_playing = audio_is_playing

                # STOP 语义：不打断正在播放的音频；在“当前句结束且音频空闲”时切断。
                if stop_on_next_sentence and not audio_is_playing:
                    self.playlist_pointer = len(self.playlist)
                    stop_on_next_sentence = False

                    waiting_between_turns = False
                    next_turn_earliest_start_at = 0.0
                    previous_frame_audio_playing = False
                    current_turn_has_audio = False

                    if len(queued_playlist) > 0:
                        self.playlist = queued_playlist.popleft()
                        self.playlist_pointer = 0
                    continue

                # 统一的“是否可以开下一句”判定：
                # - 音频仍在播放：不可开下一句
                # - 正处于句间冷却且未到时刻：不可开下一句
                # - 其余情况：可以开始下一句
                if audio_is_playing:
                    pass
                elif waiting_between_turns and now < next_turn_earliest_start_at:
                    pass
                else:
                    waiting_between_turns = False

                    this_turn_elements = self.playlist[self.playlist_pointer]

                    speaker_name = this_turn_elements.get('character_name', '未知角色')
                    emotion = this_turn_elements.get('emotion', 'normal')
                    audio_path = this_turn_elements.get('audio_path', '')
                    text = this_turn_elements.get('text', '')
                    translation = this_turn_elements.get('translation', '')

                    if 'character_name' not in this_turn_elements:
                        this_turn_elements['character_name'] = speaker_name

                    if speaker_name == self.active_slots[0]["character_name"]:
                        speaker_slot = 0
                        last_speaker_slot = 0
                    elif speaker_name == self.active_slots[1]["character_name"]:
                        speaker_slot = 1
                        last_speaker_slot = 1
                    else:
                        # 当说话人名未命中任一槽位时，沿用上一次成功命中的槽位，避免动作落到错误对象或空对象
                        speaker_slot = last_speaker_slot

                    this_turn_model = model_group[speaker_slot]
                    lip_sync_model = this_turn_model

                    has_audio_file = self._start_turn_audio(str(audio_path))
                    current_turn_has_audio = has_audio_file
                    self._try_start_emotion_motion(
                        model=this_turn_model,
                        emotion=str(emotion),
                        position=self._motion_position_for_slot(
                            speaker_slot,
                            self._active_model_versions(),
                            active_motion_facing_mode,
                        ),
                    )

                    tell_qt_this_turn_finish_queue.put(this_turn_elements)
                    overlay.set_text(speaker_name, text + '\n' + translation)
                    self.playlist_pointer += 1

                    # 无音频句子：不依赖 pygame 的“播放结束事件”，直接进入 1.5 秒冷却。
                    if not has_audio_file:
                        next_turn_earliest_start_at = now + WAIT_AFTER_NO_AUDIO_RATIO * len(text) + WAIT_SECONDS
                        waiting_between_turns = True
                        previous_frame_audio_playing = False

            if (
                self.motion_is_over
                and self.last_motion_model is not None
                and not self.force_eyes_open
                and time.time() - self.motion_finished_at > 0.5
            ):
                self.force_eyes_open = True

            glClear(GL_COLOR_BUFFER_BIT)

            for model in model_group:
                if model is not None:
                    model.Update()
            if self.force_eyes_open and self.last_motion_model is not None:
                self._force_model_eyes_open(self.last_motion_model)
            # 渲染背景图片
            # 强制恢复OpenGL状态，确保背景和UI的渲染不受Live2D的影响
            # 因为多模型下，不知道为什么，live2d-py 库渲染模型时会修改 OpenGL 上下文，抢走了纹理的绑定。
            glUseProgram(0)
            glActiveTexture(GL_TEXTURE0)
            glBindTexture(GL_TEXTURE_2D, texture)
            glColor4f(1, 1, 1, 1)

            BackgroundRen.blit(*self.BACKGROUND_POSITION)
            if self.wavHandler.Update() and lip_sync_model is not None:  # 控制说话时的嘴型
                lip_sync_model.set_parameter_value("mouth_open_y", self.wavHandler.GetRms() * self.lipSyncN)

                idle_recover_timer = time.time()

            for model in model_group:
                if model is not None:
                    model.Draw()

            if layout_editing:
                show_layout_edit_overlay()
                self._draw_layout_edit_selection_mask(layout_selected_slot)
            version_notice_overlay.draw()
            overlay.update()
            overlay.draw()
            glUseProgram(0)
            # 4、pygame刷新
            pygame.display.flip()
            frame_clock.tick(target_fps)

        try:
            if layout_editing:
                exit_layout_edit_mode()
            else:
                save_dirty_layouts()
        except Exception:
            logger.exception("退出小剧场 Live2D 布局编辑模式失败")
        try:
            pygame.mixer.music.stop()
        except Exception:
            pass
        try:
            self._dispose_model_group(model_group)
        except Exception:
            pass
        try:
            version_notice_overlay.dispose()
        except Exception:
            pass
        try:
            glDeleteTextures([texture])
        except Exception:
            pass
        release_live2d_runtime(current_runtime)
        # 结束pygame
        try:
            pygame.mixer.quit()
        except Exception:
            pass
        pygame.quit()


def run_live2d_process(change_char_queue, to_live2d_module_queue, tell_qt_this_turn_finish_queue, logging_queue):
    """Live2D 子进程入口。

    注意：该函数必须在模块顶层定义，才能在 Windows/macOS 的 spawn 模式下被 pickle。
    """
    from log import setup_worker_logging

    setup_worker_logging(logging_queue)
    script_dir = os.path.dirname(os.path.abspath(__file__))
    if script_dir not in sys.path:
        sys.path.insert(0, script_dir)

    import character
    from multi_char_live2d_module import Live2DModule

    get_char_attr = character.GetCharacterAttributes()
    live2d_player = Live2DModule(get_char_attr.character_class_list)
    live2d_player.play_live2d(change_char_queue, to_live2d_module_queue, tell_qt_this_turn_finish_queue)


if __name__ == "__main__":
    import sys
    from queue import Queue
    script_dir = os.path.dirname(os.path.abspath(__file__))
    if script_dir not in sys.path:
        sys.path.insert(0, script_dir)
    import character

    motion_queue = Queue()
    change_char_queue = Queue()
    char_getter = character.GetCharacterAttributes()
    live2d_module = Live2DModule(char_getter.character_class_list)
    live2d_module.play_live2d(motion_queue, change_char_queue,motion_queue)
