"""小剧场背景与淡入淡出合成；V2/V3 共用整模型透明纹理。"""
import pygame
from OpenGL.GL import *

from live2d_support.multi_char_scene import background_path


def draw_quad(texture=0, alpha=1.0, premultiplied=False, bounds=(-1, -1, 1, 1)):
    """在当前视口绘制背景纹理或黑幕，不依赖 Live2D 留下的着色器状态。"""
    program = int(glGetIntegerv(GL_CURRENT_PROGRAM))
    glPushAttrib(GL_ALL_ATTRIB_BITS)
    glUseProgram(0)
    glActiveTexture(GL_TEXTURE0)
    glDisable(GL_DEPTH_TEST)
    glDisable(GL_CULL_FACE)
    glDisable(GL_SCISSOR_TEST)
    glDisable(GL_LIGHTING)
    glDisable(GL_ALPHA_TEST)
    glDisable(GL_STENCIL_TEST)
    glColorMask(GL_TRUE, GL_TRUE, GL_TRUE, GL_TRUE)
    glEnable(GL_BLEND)
    glBlendEquation(GL_FUNC_ADD)
    glBlendFunc(GL_ONE if premultiplied else GL_SRC_ALPHA, GL_ONE_MINUS_SRC_ALPHA)
    glMatrixMode(GL_PROJECTION)
    glPushMatrix()
    glLoadIdentity()
    glMatrixMode(GL_MODELVIEW)
    glPushMatrix()
    glLoadIdentity()
    glMatrixMode(GL_TEXTURE)
    glPushMatrix()
    glLoadIdentity()
    if texture:
        glEnable(GL_TEXTURE_2D)
        glBindTexture(GL_TEXTURE_2D, texture)
        glTexEnvi(GL_TEXTURE_ENV, GL_TEXTURE_ENV_MODE, GL_MODULATE)
        color = alpha if premultiplied else 1.0
        glColor4f(color, color, color, alpha)
    else:
        glDisable(GL_TEXTURE_2D)
        glColor4f(0.0, 0.0, 0.0, alpha)
    glBegin(GL_QUADS)
    left, bottom, right, top = bounds
    for uv, point in (((0, 0), (left, bottom)), ((1, 0), (right, bottom)),
                      ((1, 1), (right, top)), ((0, 1), (left, top))):
        glTexCoord2f(*uv)
        glVertex2f(*point)
    glEnd()
    glPopMatrix()
    glMatrixMode(GL_MODELVIEW)
    glPopMatrix()
    glMatrixMode(GL_PROJECTION)
    glPopMatrix()
    glPopAttrib()
    # Shader program 不属于 glPushAttrib 保存的状态。
    glUseProgram(program)


class CapsuleOverlay:
    """缓存胶囊纹理；运动进度由舞台时钟提供。"""

    def __init__(self, font_loader):
        self.font_loader = font_loader
        self.texture = 0
        self.key = None

    def render_surface(self, text, viewport_size):
        """双倍尺寸绘制圆角与文字，长标题按字数分档缩小后再检查实际宽度。"""
        width, height = viewport_size
        w, h = max(80, round(width * 0.55)), max(28, round(height * 0.09))
        surface = pygame.Surface((w * 2, h * 2), pygame.SRCALPHA)
        pygame.draw.rect(surface, (255, 59, 113, 245), (0, h // 4, w * 2, h * 7 // 4), border_radius=h)
        inset = max(4, h // 6)
        pygame.draw.rect(surface, (255, 255, 255, 255),
                         (inset * 2, 0, (w - inset * 2) * 2, h * 3 // 2), border_radius=h)
        base = max(16, round(height * 0.033))
        # 英文约按半个全角字计数，避免同样宽度的英文标题过早缩小。
        units = sum(1 if char.isascii() else 2 for char in text)
        steps = max(0, (units - 48 + 15) // 16)
        font = self.font_loader(max(8, base - steps * max(1, round(height / 360))) * 2)
        label = font.render(text, True, (60, 60, 60))
        available = max(1, (w - inset * 4) * 2)
        if label.get_width() > available:
            label = pygame.transform.smoothscale(label, (available, max(1, round(
                label.get_height() * available / label.get_width()))))
        surface.blit(label, label.get_rect(center=(w, h * 3 // 4)))
        return surface

    def draw(self, text, offset, viewport_size):
        key = (text, viewport_size)
        if key != self.key:
            surface = self.render_surface(text, viewport_size)
            if not self.texture:
                self.texture = glGenTextures(1)
            glBindTexture(GL_TEXTURE_2D, self.texture)
            glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_LINEAR)
            glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_LINEAR)
            glTexImage2D(GL_TEXTURE_2D, 0, GL_RGBA, *surface.get_size(), 0, GL_RGBA,
                         GL_UNSIGNED_BYTE, pygame.image.tostring(surface, "RGBA", True))
            self.key = key
            self.size = (surface.get_width() / 2, surface.get_height() / 2)
        width, height = viewport_size
        half_w, half_h = self.size[0] / width, self.size[1] / height
        center = offset * (1 + half_w)
        draw_quad(self.texture, bounds=(center - half_w, -half_h, center + half_w, half_h))

    def dispose(self):
        if self.texture:
            glDeleteTextures([self.texture])
            self.texture = 0
        self.key = None


class StageBackgrounds:
    """先加载成功再替换背景，并回收离开当前场景后不再需要的纹理。"""

    def __init__(self, filename, size):
        """加载程序默认背景，缓存名称只使用项目背景目录内的文件名。"""
        self.default_name = filename
        self.current_name = filename
        self.size = size
        self.textures = {}
        self.prepare(filename)

    def prepare(self, filename):
        """校验并提前解码目标背景；加载失败不会改动当前纹理。"""
        if filename in self.textures:
            return
        path = background_path(filename)
        try:
            surface = pygame.image.load(str(path)).convert_alpha()
        except (pygame.error, FileNotFoundError) as exc:
            raise ValueError(str(exc)) from exc
        surface = pygame.transform.smoothscale(surface, self.size)
        bound_texture = int(glGetIntegerv(GL_TEXTURE_BINDING_2D))
        texture = glGenTextures(1)
        try:
            glBindTexture(GL_TEXTURE_2D, texture)
            glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_LINEAR)
            glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_LINEAR)
            glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_S, GL_CLAMP_TO_EDGE)
            glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_T, GL_CLAMP_TO_EDGE)
            glTexImage2D(GL_TEXTURE_2D, 0, GL_RGBA, *self.size, 0, GL_RGBA,
                         GL_UNSIGNED_BYTE, pygame.image.tostring(surface, "RGBA", True))
        except Exception:
            glDeleteTextures([texture])
            raise
        finally:
            glBindTexture(GL_TEXTURE_2D, bound_texture)
        self.textures[filename] = texture

    def show(self, filename):
        """切换到已经加载的纹理，加载动作与舞台切换时点分开。"""
        self.current_name = filename

    def draw(self):
        """绘制当前背景。"""
        draw_quad(self.textures[self.current_name])

    def prune(self, keep):
        """回收当前场景和编辑恢复之外的图片纹理。"""
        for name in list(self.textures):
            if name not in keep:
                glDeleteTextures([self.textures.pop(name)])

    def dispose(self):
        """在 OpenGL 上下文销毁前释放所有背景纹理。"""
        if self.textures:
            glDeleteTextures(list(self.textures.values()))
            self.textures.clear()


class ActorCompositor:
    """仅渐隐／渐显时离屏绘制整模型，避免篡改模型原有部件透明度。"""

    def __init__(self):
        """延迟分配可复用的离屏缓冲，普通双人播放无额外合成。"""
        self.framebuffer = 0
        self.texture = 0
        self.size = None

    def resize(self, size):
        """跟随实际视口尺寸建立完整 RGBA 帧缓冲。"""
        if size == self.size:
            return
        draw_framebuffer = int(glGetIntegerv(GL_DRAW_FRAMEBUFFER_BINDING))
        read_framebuffer = int(glGetIntegerv(GL_READ_FRAMEBUFFER_BINDING))
        bound_texture = int(glGetIntegerv(GL_TEXTURE_BINDING_2D))
        texture = glGenTextures(1)
        framebuffer = glGenFramebuffers(1)
        try:
            glBindTexture(GL_TEXTURE_2D, texture)
            glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_LINEAR)
            glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_LINEAR)
            glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_S, GL_CLAMP_TO_EDGE)
            glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_T, GL_CLAMP_TO_EDGE)
            glTexImage2D(GL_TEXTURE_2D, 0, GL_RGBA, *size, 0, GL_RGBA, GL_UNSIGNED_BYTE, None)
            glBindFramebuffer(GL_FRAMEBUFFER, framebuffer)
            glFramebufferTexture2D(GL_FRAMEBUFFER, GL_COLOR_ATTACHMENT0, GL_TEXTURE_2D, texture, 0)
            if glCheckFramebufferStatus(GL_FRAMEBUFFER) != GL_FRAMEBUFFER_COMPLETE:
                raise RuntimeError("无法建立小剧场人物渐变缓冲")
        except Exception:
            glDeleteFramebuffers(1, [framebuffer])
            glDeleteTextures([texture])
            raise
        finally:
            glBindFramebuffer(GL_DRAW_FRAMEBUFFER, draw_framebuffer)
            glBindFramebuffer(GL_READ_FRAMEBUFFER, read_framebuffer)
            glBindTexture(GL_TEXTURE_2D, bound_texture)
        self.dispose()
        self.texture = texture
        self.framebuffer = framebuffer
        self.size = size

    def draw(self, model, opacity):
        """按整模型透明度绘制，透明度为 0 时完全跳过绘制。"""
        if opacity <= 0:
            return
        if opacity >= 1:
            model.Draw()
            return
        viewport = tuple(int(value) for value in glGetIntegerv(GL_VIEWPORT))
        draw_framebuffer = int(glGetIntegerv(GL_DRAW_FRAMEBUFFER_BINDING))
        read_framebuffer = int(glGetIntegerv(GL_READ_FRAMEBUFFER_BINDING))
        program = int(glGetIntegerv(GL_CURRENT_PROGRAM))
        glPushAttrib(GL_ALL_ATTRIB_BITS)
        try:
            self.resize(viewport[2:])
            glBindFramebuffer(GL_FRAMEBUFFER, self.framebuffer)
            glViewport(0, 0, *self.size)
            glDisable(GL_SCISSOR_TEST)
            glColorMask(GL_TRUE, GL_TRUE, GL_TRUE, GL_TRUE)
            glClearColor(0, 0, 0, 0)
            glClear(GL_COLOR_BUFFER_BIT)
            model.Draw()
        finally:
            glBindFramebuffer(GL_DRAW_FRAMEBUFFER, draw_framebuffer)
            glBindFramebuffer(GL_READ_FRAMEBUFFER, read_framebuffer)
            glPopAttrib()
            glViewport(*viewport)
            glUseProgram(program)
        # Cubism 的输出使用预乘 Alpha，二次合成需一起缩放 RGB 和 Alpha。
        draw_quad(self.texture, opacity, premultiplied=True)

    def dispose(self):
        """释放临时帧缓冲和颜色纹理。"""
        if self.framebuffer:
            glDeleteFramebuffers(1, [self.framebuffer])
            self.framebuffer = 0
        if self.texture:
            glDeleteTextures([self.texture])
            self.texture = 0
        self.size = None
