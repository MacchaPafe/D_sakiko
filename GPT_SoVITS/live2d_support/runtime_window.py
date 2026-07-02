from __future__ import annotations

import os
from dataclasses import dataclass
from types import ModuleType
from typing import Callable

import pygame
from OpenGL.GL import (
    GL_TEXTURE0,
    GL_TEXTURE_2D,
    glActiveTexture,
    glBindTexture,
    glDeleteTextures,
    glEnable,
    glViewport,
)
from pygame.locals import DOUBLEBUF, OPENGL

from live2d_support.runtime_adapter import (
    Live2DVersion,
    initialize_live2d_runtime,
    load_live2d_runtime,
    release_live2d_runtime,
)

TextureRenderer = Callable[[object], object]


@dataclass(frozen=True)
class RuntimeWindowRecreateResult:
    """记录 Live2D runtime 窗口重建后的资源。"""

    runtime: ModuleType
    texture: object


def recreate_runtime_window(
        *,
        current_runtime: ModuleType | None,
        current_texture: object,
        target_version: Live2DVersion,
        display: tuple[int, int],
        window_position: str | None,
        background_path: str,
        render_texture: TextureRenderer,
) -> RuntimeWindowRecreateResult:
    """
    重建 pygame OpenGL 窗口、目标 Live2D runtime 和背景纹理。
    在切换了 live2d v2/v3 版本的模型后，需要使用此函数来重建窗口和相关资源。
    """
    release_live2d_runtime(current_runtime)
    try:
        glDeleteTextures([current_texture])
    except Exception:
        pass

    pygame.display.quit()
    pygame.display.init()
    if window_position:
        os.environ["SDL_VIDEO_WINDOW_POS"] = window_position
    pygame.display.set_mode(display, DOUBLEBUF | OPENGL)
    glViewport(0, 0, *display)

    runtime = load_live2d_runtime(target_version)
    initialize_live2d_runtime(runtime)
    glEnable(GL_TEXTURE_2D)

    texture = render_texture(pygame.image.load(background_path).convert_alpha())
    glActiveTexture(GL_TEXTURE0)
    glBindTexture(GL_TEXTURE_2D, texture)
    return RuntimeWindowRecreateResult(runtime=runtime, texture=texture)
