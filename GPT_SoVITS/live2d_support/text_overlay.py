"""Pygame text overlay mechanics shared by the single-model renderer."""
from __future__ import annotations

import contextlib
import os
import sys

script_dir = os.path.dirname(os.path.abspath(__file__))
project_dir = os.path.dirname(script_dir)
if project_dir not in sys.path:
    sys.path.insert(0, project_dir)

with open(os.devnull, "w") as devnull:
    with contextlib.redirect_stdout(devnull):
        with contextlib.redirect_stderr(devnull):
            import pygame

from OpenGL.GL import *

from log import get_logger

logger = get_logger(__name__)


def _load_cjk_font(size: int, bold: bool = False) -> pygame.font.Font:
    candidates = [
        os.path.join(project_dir, "font", "msyh.ttc"),
        "Microsoft YaHei", "Microsoft YaHei UI", "SimHei", "SimSun",
        "MS Gothic", "PingFang SC", "Hiragino Sans GB", "Heiti SC",
        "STHeiti", "Noto Sans CJK SC", "WenQuanYi Zen Hei", "Arial Unicode MS",
    ]
    for candidate in candidates:
        try:
            if os.path.isfile(candidate):
                font = pygame.font.Font(candidate, size)
            else:
                font = pygame.font.SysFont(candidate, size)
            font.set_bold(bold)
            return font
        except Exception:
            continue
    font = pygame.font.Font(None, size)
    font.set_bold(bold)
    return font


class TextOverlay:
    """Render the existing dialogue overlay without owning behaviour state."""

    def __init__(self, window_size: tuple[int, int], char_names: list[str]):
        self.win_w, self.win_h = window_size
        self.texture_id = glGenTextures(1)
        self.surface_w = int(self.win_w * 0.8)
        self.surface_h = int(self.win_h * 0.15)
        self.rect_surface = pygame.Surface((self.surface_w, self.surface_h), pygame.SRCALPHA)
        self.font_size_main = max(12, int(self.surface_h * 0.14))
        self.font_size_name = max(10, int(self.surface_h * 0.15))
        self.main_font = _load_cjk_font(self.font_size_main, bold=True)
        self.name_font = _load_cjk_font(self.font_size_name, bold=True)
        self.ui_name_box_h = int(self.surface_h * 0.22)
        self.ui_name_radius = self.ui_name_box_h // 2
        self.ui_main_radius = int(self.surface_h * 0.15)
        self.ui_border_thick = max(3, int(self.surface_h * 0.015))
        self.ui_padding_x = int(self.surface_w * 0.02)
        self.COLOR_WHITE_BG = (255, 255, 255, 220)
        self.COLOR_BLACK_BORDER = (0, 0, 0, 48)
        self.COLOR_PINK_BG = (255, 59, 113, 255)
        self.COLOR_TEXT_MAIN = (60, 60, 60)
        self.COLOR_TEXT_NAME = (255, 255, 255)
        bottom_y = -0.95
        top_y = bottom_y + (self.surface_h / self.win_h) * 2 * 0.95
        self.quad_vertices = [
            -0.8, top_y, 0.0, 0.8, top_y, 0.0,
            0.8, bottom_y, 0.0, -0.8, bottom_y, 0.0,
        ]
        self.full_text = ""
        self.current_text = ""
        self.char_pointer = 0.0
        self.typing_speed = 2
        self.current_char_name = ""
        self.is_typing = False
        try:
            raw_star = pygame.image.load("./icons/star.png").convert_alpha()
            star_size = int(self.font_size_main * 1.0)
            self.star_img = pygame.transform.smoothscale(raw_star, (star_size, star_size))
        except Exception:
            logger.debug("星星图标加载失败", exc_info=True)
            self.star_img = None
        if len(char_names) == 1:
            self.set_text(char_names[0], "...")
        else:
            self.set_text(f"{char_names[0]} & {char_names[1]}", "...")

    def set_text(self, char_name, text):
        if text == self.full_text and char_name == self.current_char_name:
            return
        self.current_char_name = char_name
        self.full_text = text
        self.char_pointer = 0.0
        self.current_text = ""
        self.is_typing = True
        self._render_texture()

    def update(self):
        if not self.is_typing:
            return
        if self.char_pointer < len(self.full_text):
            self.char_pointer += self.typing_speed
            display_count = min(int(self.char_pointer), len(self.full_text))
            new_slice = self.full_text[:display_count]
            if new_slice != self.current_text:
                self.current_text = new_slice
                self._render_texture()
            if display_count >= len(self.full_text):
                self.is_typing = False
        else:
            self.is_typing = False

    def _render_texture(self):
        char_name = self.current_char_name
        text = self.current_text
        self.rect_surface.fill((0, 0, 0, 0))
        main_box_offset_y = self.ui_name_box_h
        main_rect = pygame.Rect(
            self.ui_border_thick // 2, main_box_offset_y,
            self.surface_w - self.ui_border_thick,
            self.surface_h - main_box_offset_y - self.ui_border_thick // 2,
        )
        pygame.draw.rect(self.rect_surface, self.COLOR_WHITE_BG, main_rect, border_radius=self.ui_main_radius)
        pygame.draw.rect(self.rect_surface, self.COLOR_BLACK_BORDER, main_rect,
                         width=self.ui_border_thick, border_radius=self.ui_main_radius)
        name_box_width = int(self.surface_w * 0.15)
        name_rect = pygame.Rect(0, 0, name_box_width, self.ui_name_box_h)
        if char_name:
            pygame.draw.rect(self.rect_surface, self.COLOR_PINK_BG, name_rect, border_radius=self.ui_name_radius)
            pygame.draw.rect(self.rect_surface, (255, 255, 255, 255), name_rect,
                             width=max(2, self.ui_border_thick // 2), border_radius=self.ui_name_radius)
            name_txt_img = self.name_font.render(char_name, True, self.COLOR_TEXT_NAME)
            name_txt_rect = name_txt_img.get_rect(center=name_rect.center)
            self.rect_surface.blit(name_txt_img, name_txt_rect)
        text_start_y = main_box_offset_y + int(self.ui_name_box_h * 0.75)
        max_text_width = self.surface_w - (self.ui_padding_x * 2)
        y_offset = text_start_y
        line_spacing = int(self.font_size_main * 1.5)
        for line in self.wrap_text(text, self.main_font, max_text_width):
            txt_img = self.main_font.render(line, True, self.COLOR_TEXT_MAIN)
            self.rect_surface.blit(txt_img, (self.ui_padding_x, y_offset))
            y_offset += line_spacing
        texture_data = pygame.image.tostring(self.rect_surface, "RGBA", True)
        width, height = self.rect_surface.get_size()
        glBindTexture(GL_TEXTURE_2D, self.texture_id)
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_LINEAR)
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_LINEAR)
        glTexImage2D(GL_TEXTURE_2D, 0, GL_RGBA, width, height, 0, GL_RGBA, GL_UNSIGNED_BYTE, texture_data)
        glBindTexture(GL_TEXTURE_2D, 0)

    def draw(self):
        glPushAttrib(GL_ALL_ATTRIB_BITS)
        glUseProgram(0)
        glActiveTexture(GL_TEXTURE0)
        glEnable(GL_BLEND)
        glBlendFunc(GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA)
        glEnable(GL_TEXTURE_2D)
        glBindTexture(GL_TEXTURE_2D, self.texture_id)
        glColor4f(1, 1, 1, 1)
        glBegin(GL_QUADS)
        glTexCoord2f(0, 1); glVertex3f(*self.quad_vertices[0:3])
        glTexCoord2f(1, 1); glVertex3f(*self.quad_vertices[3:6])
        glTexCoord2f(1, 0); glVertex3f(*self.quad_vertices[6:9])
        glTexCoord2f(0, 0); glVertex3f(*self.quad_vertices[9:12])
        glEnd()
        glPopAttrib()

    @staticmethod
    def wrap_text(text, font, max_width):
        words = list(text)
        lines = []
        current_line = []
        for word in words:
            if word == "\n":
                lines.append("".join(current_line))
                current_line = []
                continue
            test_line = "".join(current_line + [word])
            width, _ = font.size(test_line)
            if width < max_width:
                current_line.append(word)
            else:
                lines.append("".join(current_line))
                current_line = [word]
        lines.append("".join(current_line))
        return lines
