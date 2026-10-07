"""图标：微软 Fluent UI System Icons 的线性（Regular）版本，SVG 文件在 assets/icons/（MIT 许可）。

图标本身不带颜色，绘制时按当前主题着色：切换浅色 / 深色后，界面重绘时图标会自动变色，不用逐个刷新。
"""
from __future__ import annotations

from functools import lru_cache

from PySide6.QtCore import QByteArray, QRect, QRectF, QSize, Qt
from PySide6.QtGui import QIcon, QIconEngine, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QWidget

from .theme import ICON_DIR, theme, tinted_svg

DEFAULT_ICON = "document_text"


def exists(name: str) -> bool:
    return bool(name) and (ICON_DIR / f"{name}.svg").exists()


def resolve(name: str | None, fallback: str = DEFAULT_ICON) -> str:
    """模板里写的图标名不存在（例如旧模板里的 emoji）时用默认图标。"""
    return name if name and exists(name) else fallback


@lru_cache(maxsize=512)
def _renderer(name: str, color: str) -> QSvgRenderer:
    return QSvgRenderer(QByteArray(tinted_svg(name, color)))


def paint(painter: QPainter, rect: QRect | QRectF, name: str, color: str) -> None:
    """图标画成正方形、居中；区域比图标宽时两边留空（按钮里图标和文字之间就多出一点距离）。"""
    rect = QRectF(rect)
    side = min(rect.width(), rect.height())
    square = QRectF(rect.center().x() - side / 2, rect.center().y() - side / 2, side, side)
    _renderer(resolve(name), color).render(painter, square)


class _ThemedIconEngine(QIconEngine):
    """每次绘制时读取当前主题的颜色，所以主题切换后图标自动变色。"""

    def __init__(self, name: str, role: str):
        super().__init__()
        self.name, self.role = resolve(name), role

    def _color(self, mode: QIcon.Mode) -> str:
        return theme.color("text3" if mode == QIcon.Mode.Disabled else self.role)

    def paint(self, painter, rect, mode, state):
        paint(painter, rect, self.name, self._color(mode))

    def pixmap(self, size, mode, state):
        pixmap = QPixmap(size)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.paint(painter, QRect(0, 0, size.width(), size.height()), mode, state)
        painter.end()
        return pixmap

    def clone(self):
        return _ThemedIconEngine(self.name, self.role)


def icon(name: str, role: str = "text2") -> QIcon:
    """role 是主题里的颜色名：text / text2 / text3 / on_accent …"""
    return QIcon(_ThemedIconEngine(name, role))


class IconView(QWidget):
    """只显示一个图标的小部件（比 QLabel 放图片好：主题切换时自动重绘成新颜色）。"""

    def __init__(self, name: str, size: int = 20, role: str = "text2", parent=None):
        super().__init__(parent)
        self._name, self._role = resolve(name), role
        self.setFixedSize(QSize(size, size))
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)

    def set_icon(self, name: str, role: str | None = None) -> None:
        self._name = resolve(name)
        self._role = role or self._role
        self.update()

    def set_role(self, role: str) -> None:
        self._role = role
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        color = self._role if self._role.startswith("#") else theme.color(self._role)  # 也可以直接给颜色
        paint(painter, self.rect(), self._name, color)
