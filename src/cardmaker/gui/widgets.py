"""通用组件：页面骨架、步骤条、用途卡片、选项卡片、文件拖放区、状态行。

颜色一律在绘制时从 theme 取，切换浅色 / 深色后自动变。
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import (Property, QEasingCurve, QEvent, QObject, QParallelAnimationGroup, QPointF,
                            QPropertyAnimation, QRectF, QSize, Qt, Signal)
from PySide6.QtGui import QColor, QFont, QFontMetrics, QImage, QPainter, QPen, QRadialGradient
from PySide6.QtWidgets import (QAbstractButton, QButtonGroup, QDialog, QFrame, QGraphicsDropShadowEffect, QHBoxLayout,
                               QLabel, QPushButton, QScrollArea, QSizePolicy, QVBoxLayout, QWidget)

from ..i18n import tr
from . import icons
from .icons import IconView
from .theme import CAPTION, STATUS_COLORS, qcolor, theme

SUPPORTED_SUFFIXES = (".pdf", ".txt", ".md")
ASSETS = Path(__file__).parent / "assets"  # 插图（磨砂玻璃风格的立体图标，用学校的生成式 AI 平台画的）


def repolish(widget: QWidget) -> None:
    """动态属性改变后让样式表重新生效。"""
    widget.style().unpolish(widget)
    widget.style().polish(widget)
    widget.update()


def label(text: str = "", role: str | None = None, wrap: bool = False) -> QLabel:
    """role：title 标题 / strong 加粗正文 / lead 小标题 / secondary 次要正文 / caption 说明 / meta 更淡的说明 / chip 标签。"""
    lbl = QLabel(tr(text))
    if role:
        lbl.setProperty("role", role)
    lbl.setWordWrap(wrap)
    return lbl


SHADOW = (16, 24, 40)  # 阴影颜色（偏冷的深灰），透明度另给


def shadow(widget: QWidget, blur: int = 32, y: int = 8, alpha: int = 22) -> QGraphicsDropShadowEffect:
    """柔和的投影（样式表画不了阴影）。阴影会画到控件外面约 blur / 2 的地方，外面要留出空间。"""
    effect = QGraphicsDropShadowEffect(widget)
    effect.setBlurRadius(blur)
    effect.setOffset(0, y)
    effect.setColor(QColor(*SHADOW, alpha))
    widget.setGraphicsEffect(effect)
    return effect


class HoverLift(QObject):
    """鼠标移上去时阴影变大、变深，控件像浮起来一点；移开再落回去（180 毫秒动画）。"""

    REST, RAISED = (16, 2, 12), (36, 12, 30)  # 模糊半径、向下偏移、透明度

    def __init__(self, widget: QWidget):
        super().__init__(widget)
        self.effect = shadow(widget, *self.REST)
        self.group = QParallelAnimationGroup(self)
        self.animations = []
        for name in (b"blurRadius", b"offset", b"color"):
            animation = QPropertyAnimation(self.effect, name, self)
            animation.setDuration(180)
            animation.setEasingCurve(QEasingCurve.Type.OutCubic)
            self.group.addAnimation(animation)
            self.animations.append(animation)
        widget.installEventFilter(self)

    def eventFilter(self, obj, event):
        if event.type() in (QEvent.Type.Enter, QEvent.Type.Leave):
            blur, y, alpha = self.RAISED if event.type() == QEvent.Type.Enter else self.REST
            self.group.stop()
            now = (self.effect.blurRadius(), self.effect.offset(), self.effect.color())
            for animation, start, end in zip(self.animations, now, (float(blur), QPointF(0, y), QColor(*SHADOW, alpha))):
                animation.setStartValue(start)
                animation.setEndValue(end)
            self.group.start()
        return False


class Backdrop(QWidget):
    """主窗口的底：浅灰上几团很淡的彩色光，像隔着磨砂玻璃看到的颜色。

    sheet 是浮在上面的白色内容面板，这里在它下面画一圈柔和的阴影（不用 QGraphicsEffect：面板里有表格，
    整块面板加特效会让滚动变慢）。
    """

    BLOBS = ((0.0, 0.0, 0.8), (1.0, 0.1, 0.65), (0.2, 1.0, 0.7))  # 每团光的位置（按窗口比例）和半径

    def __init__(self, parent=None):
        super().__init__(parent)
        self.sheet: QWidget | None = None
        theme.changed.connect(self.update)

    def paintEvent(self, event):
        p = theme.palette
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(self.rect())
        painter.fillRect(rect, QColor(p.bg))
        for (fx, fy, fr), color in zip(self.BLOBS, p.blobs):
            gradient = QRadialGradient(QPointF(rect.width() * fx, rect.height() * fy),
                                       max(rect.width(), rect.height()) * fr)
            inner, outer = QColor(color), QColor(color)
            outer.setAlpha(0)
            gradient.setColorAt(0, inner)
            gradient.setColorAt(1, outer)
            painter.fillRect(rect, gradient)
        if self.sheet is not None and self.sheet.isVisible():
            sheet = QRectF(self.sheet.geometry())
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(*SHADOW, 3))
            for spread in range(2, 26, 2):  # 由大到小叠很淡的圆角矩形，越靠近面板越深
                painter.drawRoundedRect(sheet.adjusted(-spread, -spread + 8, spread, spread + 8),
                                        24 + spread, 24 + spread)


def button(text: str, variant: str | None = None, icon: str | None = None, tooltip: str = "") -> QPushButton:
    """variant：None 白底细边框 / primary 黑底主按钮 / secondary 灰底 / ghost 无底无边框 / link 只有文字。"""
    btn = QPushButton(tr(text))
    if variant:
        btn.setProperty("variant", variant)
    if icon:
        btn.setIcon(icons.icon(icon, "on_accent" if variant == "primary" else "text"))
        btn.setIconSize(QSize(22, 16))  # 图标 16，左右各留 3，和文字隔开
    if tooltip:
        btn.setToolTip(tr(tooltip))
    btn.setCursor(Qt.CursorShape.PointingHandCursor)
    return btn


class Picture(QWidget):
    """显示一张插图（assets 里的 PNG），平滑缩放，高分屏上也清楚。"""

    def __init__(self, name: str, size: int, parent=None):
        super().__init__(parent)
        self.image = QImage(str(ASSETS / name))
        self.setFixedSize(size, size)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        painter.drawImage(QRectF(self.rect()), self.image)


def segmented(options: list[str], parent: QWidget | None = None) -> tuple[QFrame, QButtonGroup]:
    """分段标签（shadcn 的 Tabs）：灰色轨道里一排按钮，选中的那个是白色。按钮编号就是 options 里的位置。"""
    frame = QFrame(parent)
    frame.setObjectName("Tabs")
    row = QHBoxLayout(frame)
    row.setContentsMargins(3, 3, 3, 3)
    row.setSpacing(2)
    group = QButtonGroup(frame)
    for i, text in enumerate(options):
        btn = QPushButton(tr(text))
        btn.setCheckable(True)
        btn.setCursor(Qt.CursorShape.PointingHandCursor)
        group.addButton(btn, i)
        row.addWidget(btn)
    group.button(0).setChecked(True)
    return frame, group


def icon_button(name: str, tooltip: str) -> QPushButton:
    btn = QPushButton()
    btn.setProperty("variant", "icon")
    btn.setIcon(icons.icon(name, "text2"))
    btn.setIconSize(QSize(20, 20))
    btn.setToolTip(tr(tooltip))
    btn.setCursor(Qt.CursorShape.PointingHandCursor)
    return btn


class ElidedLabel(QLabel):
    """单行文字，放不下时末尾显示省略号（完整内容在悬停提示里）。"""

    def __init__(self, text: str = "", role: str | None = None, parent=None):
        super().__init__(parent)
        if role:
            self.setProperty("role", role)
        self._full = ""
        self.setMinimumWidth(40)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.setText(text)

    def full_text(self) -> str:
        return self._full

    def setText(self, text: str) -> None:
        self._full = text
        self._elide()

    def _elide(self) -> None:
        shown = self.fontMetrics().elidedText(self._full, Qt.TextElideMode.ElideRight, max(self.width(), 40))
        super().setText(shown)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._elide()

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() in (event.Type.FontChange, event.Type.StyleChange):
            self._elide()


# ---------------------------------------------------------------------- 页面骨架
class Centered(QWidget):
    """把内容限制在 max_width 以内并水平居中；窗口很宽时内容不会被拉得太长。"""

    def __init__(self, inner: QWidget, max_width: int = 760, margin: int = 40, parent=None):
        super().__init__(parent)
        self.max_width, self.margin = max_width, margin
        self._layout = QHBoxLayout(self)
        self._layout.setSpacing(0)
        self._layout.setContentsMargins(margin, 0, margin, 0)
        self._layout.addWidget(inner)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        side = max(self.margin, (self.width() - self.max_width) // 2) if self.max_width else self.margin
        if self._layout.contentsMargins().left() != side:
            self._layout.setContentsMargins(side, 0, side, 0)


class Footer(QWidget):
    """页面底部的操作栏：左边放次要操作，右边放主按钮。"""

    def __init__(self, max_width: int = 760, margin: int = 40, parent=None):
        super().__init__(parent)
        self.setObjectName("Footer")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        inner = QWidget()
        row = QHBoxLayout(inner)
        row.setContentsMargins(0, 14, 0, 14)
        row.setSpacing(8)
        self.left, self.right = QHBoxLayout(), QHBoxLayout()
        self.left.setSpacing(8)
        self.right.setSpacing(8)
        row.addLayout(self.left)
        row.addStretch(1)
        row.addLayout(self.right)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(Centered(inner, max_width, margin))


class PageFrame(QWidget):
    """向导页面：上面是可滚动、居中限宽的内容区（self.body），下面是固定的操作栏（self.footer）。

    bleed：内容区左右多留出的空白，给卡片的阴影用（内容本身仍然和操作栏对齐）。
    """

    def __init__(self, max_width: int = 760, margin: int = 40, scroll: bool = True, top: int = 36, bleed: int = 0,
                 parent=None):
        super().__init__(parent)
        self.setObjectName("Page")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        content = QWidget()
        content.setObjectName("PageContent")
        self.body = QVBoxLayout(content)
        self.body.setContentsMargins(bleed, top, bleed, 32)
        self.body.setSpacing(0)
        center = Centered(content, max_width + 2 * bleed, margin - bleed)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        if scroll:
            center.setObjectName("ScrollBody")
            area = QScrollArea()
            area.setWidgetResizable(True)
            area.setFrameShape(QFrame.Shape.NoFrame)
            area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            area.setWidget(center)
            outer.addWidget(area, 1)
        else:
            outer.addWidget(center, 1)
        self.footer = Footer(max_width, margin)
        outer.addWidget(self.footer)

    def add_heading(self, title: str, subtitle: str = "") -> tuple[QLabel, QLabel]:
        """页面标题（22 号）+ 一行说明。"""
        title_label = label(title, "title")
        subtitle_label = label(subtitle, "secondary", wrap=True)
        self.body.addWidget(title_label)
        self.body.addSpacing(6)
        self.body.addWidget(subtitle_label)
        self.body.addSpacing(28)
        return title_label, subtitle_label


class Dialog(QDialog):
    """对话框骨架：内容区（self.body）+ 底部操作栏（self.footer）。"""

    def __init__(self, parent, title: str, heading: str = "", subtitle: str = "", width: int = 560):
        super().__init__(parent)
        self.setWindowTitle(tr(title))
        self.setMinimumWidth(width)
        content = QWidget()
        self.body = QVBoxLayout(content)
        self.body.setContentsMargins(28, 24, 28, 24)
        self.body.setSpacing(16)
        if heading:
            head = QVBoxLayout()
            head.setSpacing(4)
            head.addWidget(label(heading, "dialogtitle"))
            if subtitle:
                self.subtitle = label(subtitle, "secondary", wrap=True)
                head.addWidget(self.subtitle)
            self.body.addLayout(head)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        outer.addWidget(content, 1)
        self.footer = Footer(max_width=0, margin=28)
        outer.addWidget(self.footer)

    def showEvent(self, event):
        super().showEvent(event)
        if self.layout().hasHeightForWidth():  # 有折行的文字：按实际宽度把高度补够，不然最后一行会被截掉
            self.resize(self.width(), max(self.height(), self.heightForWidth(self.width())))


# ---------------------------------------------------------------------- 步骤条
class StepIndicator(QWidget):
    """顶部步骤条，像苹果的分段控件：半透明的玻璃轨道上，当前步骤是一块浮起来的白色胶囊，
    换步骤时胶囊滑过去。做完的步骤打勾，没到的是灰色。"""

    DOT, GAP, PAD, INSET, HEIGHT = 20, 8, 14, 4, 40  # 圆点直径、圆点和文字的间距、每段左右留白、胶囊离轨道边、高度

    def __init__(self, steps: list[str], parent=None):
        super().__init__(parent)
        self.steps = [tr(step) for step in steps]
        self.current = 0
        self._thumb = 0.0  # 白色胶囊停在第几段（滑动途中是小数）
        self.animation = QPropertyAnimation(self, b"thumb", self)
        self.animation.setDuration(280)
        self.animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.setFixedHeight(self.HEIGHT)
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        theme.changed.connect(self.update)

    def _get_thumb(self) -> float:
        return self._thumb

    def _set_thumb(self, value: float) -> None:
        self._thumb = value
        self.update()

    thumb = Property(float, _get_thumb, _set_thumb)

    def _bold(self) -> QFont:
        font = QFont(self.font())
        font.setWeight(QFont.Weight.DemiBold)
        return font

    def _segments(self) -> list[QRectF]:
        metrics = QFontMetrics(self._bold())  # 按粗体算宽度，切换步骤时位置不跳
        x, rects = float(self.INSET), []
        for text in self.steps:
            width = self.PAD + self.DOT + self.GAP + metrics.horizontalAdvance(text) + 2 + self.PAD
            rects.append(QRectF(x, self.INSET, width, self.HEIGHT - 2 * self.INSET))
            x += width
        return rects

    def sizeHint(self) -> QSize:
        return QSize(int(self._segments()[-1].right()) + self.INSET, self.HEIGHT)

    def set_current(self, index: int) -> None:
        if index == self.current:
            return
        self.current = index
        self.animation.stop()
        self.animation.setStartValue(self._thumb)
        self.animation.setEndValue(float(index))
        self.animation.start()

    def state(self, index: int) -> str:
        return "done" if index < self.current else "current" if index == self.current else "todo"

    def paintEvent(self, event):
        p = theme.palette
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        track = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        painter.setPen(QPen(qcolor(p.glass_border), 1))
        painter.setBrush(qcolor(p.glass))
        painter.drawRoundedRect(track, track.height() / 2, track.height() / 2)

        segments = self._segments()
        index = min(int(self._thumb), len(segments) - 1)
        a, b, f = segments[index], segments[min(index + 1, len(segments) - 1)], self._thumb - index
        thumb = QRectF(a.x() + (b.x() - a.x()) * f, a.y(), a.width() + (b.width() - a.width()) * f, a.height())
        radius = thumb.height() / 2
        painter.setPen(Qt.PenStyle.NoPen)
        for spread, alpha in ((3.0, 8), (1.5, 12)):  # 胶囊下面一点点阴影，看起来是浮起来的
            painter.setBrush(QColor(*SHADOW, alpha))
            painter.drawRoundedRect(thumb.adjusted(-spread, -spread + 2, spread, spread + 2),
                                    radius + spread, radius + spread)
        painter.setBrush(QColor(p.raised))
        painter.drawRoundedRect(thumb, radius, radius)

        normal, bold = QFont(self.font()), self._bold()
        number = QFont(bold)
        number.setPixelSize(CAPTION)
        for i, (segment, text) in enumerate(zip(segments, self.steps)):
            state = self.state(i)
            dot = QRectF(segment.x() + self.PAD, segment.center().y() - self.DOT / 2, self.DOT, self.DOT)
            if state == "current":
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(QColor(p.accent))
                painter.drawEllipse(dot)
                painter.setPen(QColor(p.on_accent))
                painter.setFont(number)
                painter.drawText(dot, Qt.AlignmentFlag.AlignCenter, str(i + 1))
            elif state == "done":
                icons.paint(painter, dot.adjusted(2, 2, -2, -2), "checkmark_circle", p.text2)
            else:
                painter.setPen(QPen(QColor(p.text3), 1))
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.drawEllipse(dot.adjusted(0.5, 0.5, -0.5, -0.5))
                painter.setFont(number)
                painter.drawText(dot, Qt.AlignmentFlag.AlignCenter, str(i + 1))
            painter.setFont(bold if state == "current" else normal)
            painter.setPen(QColor({"current": p.text, "done": p.text2, "todo": p.text3}[state]))
            painter.drawText(QRectF(dot.right() + self.GAP, 0, segment.right() - dot.right(), self.height()),
                             Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, text)


# ---------------------------------------------------------------------- 卡片与选项
class Tile(QFrame):
    """第一步里的一张用途卡片：图标 + 标题 + 一行说明，细节在悬停提示里。双击直接进入下一步。"""

    clicked = Signal()
    double_clicked = Signal()

    def __init__(self, icon: str, title: str, text: str, tooltip: str = "", dashed: bool = False, parent=None):
        super().__init__(parent)
        self.setObjectName("Tile")
        self.setProperty("dashed", "true" if dashed else "false")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedHeight(68)
        self.setMinimumWidth(220)
        if tooltip:
            self.setToolTip(tr(tooltip))
        row = QHBoxLayout(self)
        row.setContentsMargins(16, 12, 14, 12)
        row.setSpacing(14)
        self.icon = IconView(icon, 20, "text2")
        row.addWidget(self.icon, 0, Qt.AlignmentFlag.AlignVCenter)
        texts = QVBoxLayout()
        texts.setSpacing(2)
        self.title = label(title, "strong")
        self.text = ElidedLabel(tr(text), "caption")
        texts.addWidget(self.title)
        texts.addWidget(self.text)
        row.addLayout(texts, 1)
        self.check = IconView("checkmark_circle_filled", 18, "text")
        self.check.hide()
        row.addWidget(self.check, 0, Qt.AlignmentFlag.AlignVCenter)
        self.checked = False
        self.set_checked(False)

    def set_checked(self, checked: bool) -> None:
        self.checked = checked
        self.setProperty("checked", "true" if checked else "false")
        repolish(self)
        self.check.setVisible(checked)
        self.icon.set_role("text" if checked else "text2")

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.double_clicked.emit()


class ChoiceCard(QAbstractButton):
    """单选的选项卡片：标题 + 一行说明（例如「填空卡」「选择题」）。放进 QButtonGroup 里互斥。"""

    def __init__(self, title: str, description: str = "", parent=None):
        super().__init__(parent)
        self.setText(tr(title))
        self.description = tr(description)
        self.setCheckable(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        self.setAttribute(Qt.WidgetAttribute.WA_Hover)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setFixedHeight(64 if description else 44)

    def _fonts(self) -> tuple[QFont, QFont]:
        title = QFont(self.font())
        title.setWeight(QFont.Weight.DemiBold)
        caption = QFont(self.font())
        caption.setPixelSize(CAPTION)
        return title, caption

    def sizeHint(self) -> QSize:
        title, caption = self._fonts()
        width = max(QFontMetrics(title).horizontalAdvance(self.text()),
                    QFontMetrics(caption).horizontalAdvance(self.description))
        return QSize(max(160, width + 60), self.height())

    def minimumSizeHint(self) -> QSize:
        return QSize(120, self.height())

    def paintEvent(self, event):
        p = theme.palette
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        hovered, checked = self.underMouse(), self.isChecked()
        border = p.text if checked else p.border_strong if hovered else p.border
        painter.setPen(QPen(QColor(border), 1))
        painter.setBrush(QColor(p.hover if hovered and not checked else p.surface))
        painter.drawRoundedRect(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), 10, 10)

        cy = self.height() / 2
        dot = QRectF(16, cy - 8, 16, 16)
        if checked:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(p.text))
            painter.drawEllipse(dot)
            painter.setBrush(QColor(p.surface))
            painter.drawEllipse(dot.adjusted(5, 5, -5, -5))
        else:
            painter.setPen(QPen(QColor(p.border_strong), 1))
            painter.setBrush(QColor(p.surface))
            painter.drawEllipse(dot.adjusted(0.5, 0.5, -0.5, -0.5))

        title_font, caption_font = self._fonts()
        x, width = 44, self.width() - 44 - 14
        align = Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
        painter.setFont(title_font)
        painter.setPen(QColor(p.text))
        title = QFontMetrics(title_font).elidedText(self.text(), Qt.TextElideMode.ElideRight, width)
        if not self.description:
            painter.drawText(QRectF(x, 0, width, self.height()), align, title)
            return
        painter.drawText(QRectF(x, cy - 20, width, 20), align, title)
        painter.setFont(caption_font)
        painter.setPen(QColor(p.text2))
        desc = QFontMetrics(caption_font).elidedText(self.description, Qt.TextElideMode.ElideRight, width)
        painter.drawText(QRectF(x, cy + 1, width, 18), align, desc)


class StatusLine(QWidget):
    """一行状态说明，前面带状态图标：ok 成功（绿）/ warn 提醒（橙）/ error 失败（红）/ info 说明 / plain 无图标。"""

    ICONS = {"ok": "checkmark_circle", "warn": "warning", "error": "error_circle", "info": "info"}

    def __init__(self, text: str = "", kind: str = "plain", parent=None):
        super().__init__(parent)
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)
        self.icon = IconView("info", 16, "text2")
        self.label = label("", "secondary", wrap=True)
        self.label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        row.addWidget(self.icon, 0, Qt.AlignmentFlag.AlignTop)
        row.addWidget(self.label, 1)
        self.kind = kind
        self.set(text, kind)

    def set(self, text: str, kind: str = "plain") -> None:
        self.kind = kind
        self.label.setText(tr(text))
        name = self.ICONS.get(kind)
        self.icon.setVisible(bool(name))
        if name:
            self.icon.set_icon(name, STATUS_COLORS.get(kind, "text2"))
        self.icon.setContentsMargins(0, 0, 0, 0)

    def setText(self, text: str) -> None:
        self.set(text, "plain")

    def text(self) -> str:
        return self.label.text()


# ---------------------------------------------------------------------- 文件拖放区
class DropZone(QFrame):
    """把文件拖进来，或点一下选择文件。"""

    file_chosen = Signal(Path)
    browse_clicked = Signal()

    def __init__(self, parent=None, title: str = "拖入文件，或点击选择", hint: str = "PDF、TXT",
                 min_height: int = 168):
        super().__init__(parent)
        self.setObjectName("DropZone")
        self.setAcceptDrops(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setMinimumHeight(min_height)
        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.setSpacing(6)
        self.icon = Picture("upload.png", 64)
        self.title = label(title)
        self.title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.hint = label(hint, "caption", wrap=True)
        self.hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.hint.setMinimumWidth(360)  # 不然一行说明会折出只剩一个字的第二行
        layout.addWidget(self.icon, 0, Qt.AlignmentFlag.AlignHCenter)
        layout.addSpacing(4)
        layout.addWidget(self.title)
        layout.addWidget(self.hint)

    @staticmethod
    def _first_supported(event) -> Path | None:
        for url in event.mimeData().urls():
            path = Path(url.toLocalFile())
            if path.suffix.lower() in SUPPORTED_SUFFIXES:
                return path
        return None

    def _set_active(self, active: bool) -> None:
        self.setProperty("active", "true" if active else "false")
        repolish(self)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self.rect().contains(event.position().toPoint()):
            self.browse_clicked.emit()

    def dragEnterEvent(self, event):
        if self._first_supported(event):
            event.acceptProposedAction()
            self._set_active(True)

    def dragLeaveEvent(self, event):
        self._set_active(False)

    def dropEvent(self, event):
        self._set_active(False)
        path = self._first_supported(event)
        if path:
            self.file_chosen.emit(path)
