from __future__ import annotations

import os
import sys
import unittest
from types import ModuleType
from unittest.mock import patch

from pygame.locals import DOUBLEBUF, OPENGL

script_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if script_dir not in sys.path:
    sys.path.insert(0, script_dir)

from live2d_support import runtime_window
from live2d_support.runtime_window import recreate_runtime_window


class FakeLoadedImage:
    """测试用 pygame 图片对象。"""

    def __init__(self, surface: object) -> None:
        """记录 convert_alpha 后要返回的 surface。"""
        self.surface = surface

    def convert_alpha(self) -> object:
        """模拟 pygame 图片转换为带 alpha 的 surface。"""
        return self.surface


class Live2DRuntimeWindowTestCase(unittest.TestCase):
    """测试 Live2D runtime 窗口重建事务。"""

    def test_recreate_runtime_window_rebuilds_runtime_and_texture(self) -> None:
        """验证 runtime、display 和背景 texture 会按共同事务重建。"""
        old_runtime = ModuleType("old_runtime")
        new_runtime = ModuleType("new_runtime")
        converted_surface = object()
        rendered_texture = object()
        rendered_surfaces: list[object] = []

        def render_texture(surface: object) -> object:
            """记录传入 renderer 的 surface 并返回测试 texture。"""
            rendered_surfaces.append(surface)
            return rendered_texture

        with (
            patch.dict(os.environ, {}, clear=True),
            patch("live2d_support.runtime_window.release_live2d_runtime") as release_runtime,
            patch("live2d_support.runtime_window.glDeleteTextures") as delete_textures,
            patch("live2d_support.runtime_window.pygame.display.quit") as display_quit,
            patch("live2d_support.runtime_window.pygame.display.init") as display_init,
            patch("live2d_support.runtime_window.pygame.display.set_mode") as set_mode,
            patch("live2d_support.runtime_window.glViewport") as viewport,
            patch("live2d_support.runtime_window.load_live2d_runtime", return_value=new_runtime) as load_runtime,
            patch("live2d_support.runtime_window.initialize_live2d_runtime") as initialize_runtime,
            patch("live2d_support.runtime_window.glEnable") as enable_texture,
            patch("live2d_support.runtime_window.pygame.image.load", return_value=FakeLoadedImage(converted_surface)) as load_image,
            patch("live2d_support.runtime_window.glActiveTexture") as active_texture,
            patch("live2d_support.runtime_window.glBindTexture") as bind_texture,
        ):
            result = recreate_runtime_window(
                current_runtime=old_runtime,
                current_texture="old-texture",
                target_version="v3",
                display=(640, 480),
                window_position="100,200",
                background_path="background.png",
                render_texture=render_texture,
            )
            self.assertEqual(os.environ["SDL_VIDEO_WINDOW_POS"], "100,200")

        self.assertIs(result.runtime, new_runtime)
        self.assertIs(result.texture, rendered_texture)
        release_runtime.assert_called_once_with(old_runtime)
        delete_textures.assert_called_once_with(["old-texture"])
        display_quit.assert_called_once_with()
        display_init.assert_called_once_with()
        set_mode.assert_called_once_with((640, 480), DOUBLEBUF | OPENGL)
        viewport.assert_called_once_with(0, 0, 640, 480)
        load_runtime.assert_called_once_with("v3")
        initialize_runtime.assert_called_once_with(new_runtime)
        enable_texture.assert_called_once()
        load_image.assert_called_once_with("background.png")
        self.assertEqual(rendered_surfaces, [converted_surface])
        active_texture.assert_called_once_with(runtime_window.GL_TEXTURE0)
        bind_texture.assert_called_once_with(runtime_window.GL_TEXTURE_2D, rendered_texture)


if __name__ == "__main__":
    unittest.main()
