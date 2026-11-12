"""界面主题：浅色 / 深色两套配色、三级字号和全局样式表。界面上的颜色都从这里取，改风格只改这个文件。

设计约定
- 外层（参考苹果的磨砂玻璃）：最底下是浅灰底上几团很淡的彩色光；上面浮着一块白色圆角面板（Sheet）放页面内容。
  放在玻璃底上的控件（步骤条、顶部按钮）是半透明白的胶囊；当前步骤是一块浮起来的白色胶囊，换步骤时滑过去。
- 面板里的控件照 shadcn/ui（https://ui.shadcn.com）：中性灰配色，细边框，圆角 8（卡片 14），
  按钮和输入框高 36、文字 14 号中等粗细。按钮分 primary（黑底）、默认（白底细边框）、secondary（灰底）、
  ghost（无底无边框）、link（只有文字）、icon（方形图标）；悬停变浅灰，按下再深一点，禁用变灰。
- 彩色只用在：插图（assets 里磨砂玻璃风格的立体图标）、进度条、状态图标、卡片预览里强调的词。
  深色主题反过来：主按钮是白底黑字。
- 字号：标题 24、正文 14、说明 12（像素），全部用系统无衬线字体（中文苹方、韩文 Apple SD Gothic Neo）。
- 间距取 4 的倍数：4 / 8 / 12 / 16 / 24 / 32 / 40。
- 图标用微软 Fluent UI System Icons 的线性（Regular）版本，按主题实时着色，见 icons.py。
"""
from __future__ import annotations

import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QObject, QSettings, Qt, Signal
from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QApplication

from ..config import app_home

TITLE, BODY, CAPTION = 24, 14, 12  # 三级字号（像素）
STATUS_COLORS = {"ok": "#16A34A", "warn": "#D97706", "error": "#DC2626"}  # 状态图标：成功、提醒、失败

ICON_DIR = Path(__file__).parent / "assets" / "icons"
MODES = {"system": "跟随系统", "light": "浅色", "dark": "深色"}


@dataclass(frozen=True)
class Palette:
    name: str
    bg: str  # 窗口底色
    blobs: tuple[str, str, str]  # 底色上的三团光：左上、右上、左下
    glass: str  # 玻璃底上的半透明控件
    glass_border: str
    raised: str  # 浮起来的胶囊（当前步骤）
    surface: str  # 面板、卡片、输入框
    subtle: str  # 表格悬停行、禁用的输入框
    hover: str  # 按钮悬停、灰底按钮、标签
    selected: str  # 按下、进度条底
    border: str  # 细边框、分隔线
    border_strong: str  # 勾选框、虚线框、悬停时的输入框
    ring: str  # 输入框获得焦点
    text: str  # 主要文字
    text2: str  # 次要文字
    text3: str  # 说明、占位、禁用的文字
    accent: str  # 主按钮（浅色主题是黑色）
    accent_hover: str
    accent_pressed: str
    on_accent: str
    highlight: str  # 进度条、卡片预览里强调的词（蓝色，和 Anki 卡片上的一样）
    highlight_soft: str  # 选中文字的底色

    @property
    def is_dark(self) -> bool:
        return self.name == "dark"


LIGHT = Palette(
    name="light", bg="#EDF0F3", blobs=("#D3EBE3", "#D6E2F8", "#E5E0F6"), glass="rgba(255, 255, 255, 120)",
    glass_border="rgba(255, 255, 255, 210)", raised="#FFFFFF", surface="#FFFFFF", subtle="#FAFAFA",
    hover="#F5F5F5", selected="#EBEBEB", border="#E5E5E5", border_strong="#D4D4D4", ring="#A3A3A3",
    text="#0A0A0A", text2="#737373", text3="#A3A3A3", accent="#171717", accent_hover="#2E2E2E",
    accent_pressed="#0A0A0A", on_accent="#FAFAFA", highlight="#2563EB", highlight_soft="#EAF0FE")

DARK = Palette(
    name="dark", bg="#0F1113", blobs=("#12302A", "#16223B", "#221B35"), glass="rgba(255, 255, 255, 14)",
    glass_border="rgba(255, 255, 255, 26)", raised="#30343B", surface="#171717", subtle="#1C1C1C",
    hover="#262626", selected="#2E2E2E", border="#2A2A2A", border_strong="#3A3A3A", ring="#737373",
    text="#FAFAFA", text2="#A3A3A3", text3="#737373", accent="#E5E5E5", accent_hover="#F5F5F5",
    accent_pressed="#D4D4D4", on_accent="#171717", highlight="#6AA9FF", highlight_soft="#1D2740")


def qcolor(value: str) -> QColor:
    """样式表里的颜色转成 QColor：支持 #RRGGBB 和 rgba(r, g, b, a)（a 是 0～255）。"""
    if value.startswith("rgba("):
        return QColor(*(int(x) for x in value[5:-1].split(",")))
    return QColor(value)


def tinted_svg(name: str, color: str) -> bytes:
    """读取图标 SVG 并换成指定颜色（Fluent 图标本身不带颜色，给根节点加 fill 即可）。"""
    return (ICON_DIR / f"{name}.svg").read_bytes().replace(b"<svg ", f'<svg fill="{color}" '.encode(), 1)


def _write_assets(p: Palette) -> dict[str, str]:
    """样式表里的小图片（勾选框的对勾、下拉箭头）只能引用文件，按主题着色后写到临时目录。

    临时目录路径里没有空格，样式表的 url() 才认。
    """
    if hasattr(os,"getuid"):
        name= f"cardmaker-ui-{os.getuid()}"
    else :
        name="cardmaker-ui"
    folder = Path(tempfile.gettempdir()) / name
    folder.mkdir(exist_ok=True)
    assets = {}
    for key, name, color in (("check", "checkmark", p.on_accent), ("down", "chevron_down", p.text2),
                             ("up", "chevron_up", p.text2), ("down_disabled", "chevron_down", p.text3)):
        path = folder / f"{key}-{p.name}.svg"
        path.write_bytes(tinted_svg(name, color))
        assets[key] = path.as_posix()
    return assets


def qpalette(p: Palette) -> QPalette:
    """Qt 自带控件（Fusion 风格）用的调色板，和样式表保持一致。"""
    pal = QPalette()
    roles = {
        QPalette.ColorRole.Window: p.bg, QPalette.ColorRole.WindowText: p.text,
        QPalette.ColorRole.Base: p.surface, QPalette.ColorRole.AlternateBase: p.subtle,
        QPalette.ColorRole.Text: p.text, QPalette.ColorRole.Button: p.surface,
        QPalette.ColorRole.ButtonText: p.text, QPalette.ColorRole.BrightText: p.text,
        QPalette.ColorRole.PlaceholderText: p.text2, QPalette.ColorRole.Highlight: p.highlight_soft,
        QPalette.ColorRole.HighlightedText: p.text, QPalette.ColorRole.ToolTipBase: p.accent,
        QPalette.ColorRole.ToolTipText: p.on_accent, QPalette.ColorRole.Link: p.text,
        QPalette.ColorRole.LinkVisited: p.text, QPalette.ColorRole.Light: p.surface,
        QPalette.ColorRole.Midlight: p.hover, QPalette.ColorRole.Mid: p.border_strong,
        QPalette.ColorRole.Dark: p.border_strong, QPalette.ColorRole.Shadow: p.border_strong,
    }
    for role, color in roles.items():
        pal.setColor(role, QColor(color))
    for role in (QPalette.ColorRole.Text, QPalette.ColorRole.WindowText, QPalette.ColorRole.ButtonText):
        pal.setColor(QPalette.ColorGroup.Disabled, role, QColor(p.text3))
    return pal


def stylesheet(p: Palette) -> str:
    a = _write_assets(p)
    return f"""
* {{ outline: none; }}
QWidget {{ color: {p.text}; font-size: {BODY}px; }}
QMainWindow {{ background: {p.bg}; }}
QDialog, QMessageBox {{ background: {p.surface}; }}
#Page, #PageContent, #Header, #Footer {{ background: transparent; }}
#Divider {{ background: {p.border}; }}
QDialog #Footer {{ border-top: 1px solid {p.border}; }}
QFrame#Sheet {{ background: {p.surface}; border: 1px solid {p.glass_border}; border-radius: 24px; }}

/* 文字 */
QLabel {{ background: transparent; }}
QLabel[role="title"], QLabel[role="PageTitle"] {{ font-size: {TITLE}px; font-weight: 600; }}
QLabel[role="dialogtitle"] {{ font-size: 18px; font-weight: 600; }}
QLabel[role="AppTitle"] {{ font-size: 17px; font-weight: 600; }}
QLabel[role="strong"] {{ font-weight: 600; }}
QLabel[role="lead"], QLabel[role="Big"] {{ font-size: 16px; font-weight: 600; }}
QLabel[role="cardtitle"] {{ font-size: 18px; font-weight: 600; }}
QLabel[role="secondary"], QLabel[role="Hint"] {{ color: {p.text2}; }}
QLabel[role="AppSubtitle"] {{ color: {p.text2}; font-size: {CAPTION}px; }}
QLabel[role="caption"] {{ font-size: {CAPTION}px; color: {p.text2}; }}
QLabel[role="meta"] {{ font-size: {CAPTION}px; color: {p.text3}; }}
/* 标签（shadcn 的 Badge）：chip 灰底，tag 白底细边框 */
QLabel[role="chip"], QLabel[role="tag"] {{
    color: {p.text}; border-radius: 6px; padding: 2px 8px; font-size: {CAPTION}px; font-weight: 500;
}}
QLabel[role="chip"] {{ background: {p.hover}; border: 1px solid {p.hover}; }}
QLabel[role="tag"] {{ background: {p.surface}; border: 1px solid {p.border}; }}

QToolTip {{
    color: {p.on_accent}; background: {p.accent}; border: none; padding: 6px 10px; border-radius: 6px;
    font-size: {CAPTION}px;
}}

/* 按钮（照 shadcn/ui）：默认白底细边框；primary 黑底；secondary 灰底；ghost 无底无边框；link 只有文字；
   icon 方形图标按钮。高 36（文字 20 + 上下各 7 + 边框 2），圆角 8 */
QPushButton {{
    background: {p.surface}; color: {p.text}; border: 1px solid {p.border}; border-radius: 8px;
    padding: 7px 16px; min-height: 20px; font-weight: 500;
}}
QPushButton:hover {{ background: {p.hover}; }}
QPushButton:pressed, QPushButton:checked {{ background: {p.selected}; }}
QPushButton:disabled {{ color: {p.text3}; background: {p.surface}; border-color: {p.border}; }}
QPushButton[variant="primary"], QPushButton#Primary {{
    background: {p.accent}; color: {p.on_accent}; border-color: {p.accent}; padding: 7px 20px;
}}
QPushButton[variant="primary"]:hover, QPushButton#Primary:hover {{ background: {p.accent_hover}; border-color: {p.accent_hover}; }}
QPushButton[variant="primary"]:pressed, QPushButton#Primary:pressed {{
    background: {p.accent_pressed}; border-color: {p.accent_pressed};
}}
QPushButton[variant="primary"]:disabled, QPushButton#Primary:disabled {{
    background: {p.border}; border-color: {p.border}; color: {p.text3};
}}
QPushButton[variant="secondary"] {{ background: {p.hover}; border-color: {p.hover}; }}
QPushButton[variant="secondary"]:hover {{ background: {p.selected}; border-color: {p.selected}; }}
QPushButton[variant="ghost"] {{ background: transparent; border-color: transparent; }}
QPushButton[variant="ghost"]:hover {{ background: {p.hover}; border-color: {p.hover}; }}
QPushButton[variant="ghost"]:pressed {{ background: {p.selected}; border-color: {p.selected}; }}
QPushButton[variant="ghost"]:disabled {{ background: transparent; border-color: transparent; }}
QPushButton[variant="link"], QPushButton#Link {{
    background: transparent; border: none; color: {p.text2}; padding: 4px 0; min-height: 0;
}}
QPushButton[variant="link"]:hover, QPushButton#Link:hover {{ color: {p.text}; text-decoration: underline; background: transparent; }}
QPushButton[variant="link"]:disabled, QPushButton#Link:disabled {{ color: {p.text3}; }}
QPushButton[variant="icon"] {{
    background: transparent; border: 1px solid transparent; border-radius: 8px; padding: 0;
    min-width: 34px; max-width: 34px; min-height: 34px; max-height: 34px;
}}
QPushButton[variant="icon"]:hover {{ background: {p.hover}; border-color: {p.hover}; }}
QPushButton[variant="icon"]:pressed {{ background: {p.selected}; border-color: {p.selected}; }}
QPushButton::menu-indicator {{
    image: url({a["down"]}); width: 14px; height: 14px;
    subcontrol-origin: padding; subcontrol-position: center right; right: 10px;
}}
QPushButton[menu="true"] {{ padding-right: 32px; }}
/* 顶部玻璃底上的按钮：半透明胶囊（属于外层框架） */
#Header QPushButton {{ background: {p.glass}; border: 1px solid {p.glass_border}; border-radius: 17px; padding: 6px 14px; }}
#Header QPushButton:hover {{ background: {p.raised}; }}

/* 分段标签（shadcn 的 Tabs）：灰色轨道里一排按钮，选中的是白色 */
QFrame#Tabs {{ background: {p.hover}; border: none; border-radius: 10px; }}
QFrame#Tabs QPushButton {{
    background: transparent; color: {p.text2}; border: 1px solid transparent; border-radius: 7px;
    padding: 3px 12px; min-height: 20px;
}}
QFrame#Tabs QPushButton:hover {{ color: {p.text}; background: transparent; }}
QFrame#Tabs QPushButton:checked {{ background: {p.surface}; color: {p.text}; border-color: {p.border}; }}

/* 输入框：高 36，圆角 8，获得焦点时边框变深 */
QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QPlainTextEdit, QTextEdit {{
    background: {p.surface}; color: {p.text}; border: 1px solid {p.border}; border-radius: 8px;
    selection-background-color: {p.highlight_soft}; selection-color: {p.text};
}}
QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox {{ padding: 7px 12px; min-height: 20px; }}
QPlainTextEdit, QTextEdit {{ padding: 8px 12px; }}
QLineEdit:hover, QComboBox:hover, QSpinBox:hover, QDoubleSpinBox:hover, QPlainTextEdit:hover {{
    border-color: {p.border_strong};
}}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus, QPlainTextEdit:focus, QTextEdit:focus {{
    border-color: {p.ring};
}}
QLineEdit:disabled, QComboBox:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled {{
    color: {p.text3}; background: {p.subtle}; border-color: {p.border};
}}
QComboBox {{ padding-right: 32px; combobox-popup: 0; }}
QComboBox QLineEdit {{ border: none; padding: 0; margin: 0; background: transparent; }}
QComboBox::drop-down {{ border: none; width: 32px; subcontrol-origin: padding; subcontrol-position: center right; }}
QComboBox::down-arrow {{ image: url({a["down"]}); width: 14px; height: 14px; }}
QComboBox::down-arrow:disabled {{ image: url({a["down_disabled"]}); }}
QComboBox QAbstractItemView {{
    background: {p.surface}; border: 1px solid {p.border}; border-radius: 8px; padding: 4px;
    selection-background-color: {p.hover}; selection-color: {p.text}; outline: none;
}}
QComboBox QAbstractItemView::item {{ min-height: 30px; padding: 0 8px; border-radius: 6px; }}
QComboBox QAbstractItemView::item:hover, QComboBox QAbstractItemView::item:selected {{
    background: {p.hover}; color: {p.text};
}}
QSpinBox, QDoubleSpinBox {{ padding-right: 28px; }}
QSpinBox::up-button, QSpinBox::down-button, QDoubleSpinBox::up-button, QDoubleSpinBox::down-button {{
    subcontrol-origin: border; width: 20px; border: none; background: transparent; margin-right: 6px;
}}
QSpinBox::up-button, QDoubleSpinBox::up-button {{ subcontrol-position: top right; margin-top: 5px; }}
QSpinBox::down-button, QDoubleSpinBox::down-button {{ subcontrol-position: bottom right; margin-bottom: 5px; }}
QSpinBox::up-button:hover, QSpinBox::down-button:hover, QDoubleSpinBox::up-button:hover,
QDoubleSpinBox::down-button:hover {{ background: {p.hover}; border-radius: 4px; }}
QSpinBox::up-arrow, QDoubleSpinBox::up-arrow {{ image: url({a["up"]}); width: 10px; height: 10px; }}
QSpinBox::down-arrow, QDoubleSpinBox::down-arrow {{ image: url({a["down"]}); width: 10px; height: 10px; }}
QPlainTextEdit#Code {{ background: {p.subtle}; font-family: Menlo; font-size: 13px; }}

/* 勾选框：选中是黑底白勾；单选框：选中是黑边加中间一个黑点（深色主题反过来） */
QCheckBox, QRadioButton {{ spacing: 8px; background: transparent; }}
QCheckBox::indicator, QTableView::indicator {{
    width: 14px; height: 14px; border: 1px solid {p.border_strong}; border-radius: 4px; background: {p.surface};
}}
QCheckBox::indicator:hover, QTableView::indicator:hover {{ border-color: {p.text3}; }}
QCheckBox::indicator:checked, QTableView::indicator:checked {{
    background: {p.accent}; border-color: {p.accent}; image: url({a["check"]});
}}
QCheckBox::indicator:disabled {{ background: {p.subtle}; border-color: {p.border}; }}
QRadioButton::indicator {{
    width: 14px; height: 14px; border: 1px solid {p.border_strong}; border-radius: 8px; background: {p.surface};
}}
QRadioButton::indicator:hover {{ border-color: {p.text3}; }}
QRadioButton::indicator:checked {{
    border-color: {p.accent};
    background: qradialgradient(cx:0.5, cy:0.5, radius:0.5, fx:0.5, fy:0.5,
                                stop:0 {p.accent}, stop:0.5 {p.accent}, stop:0.6 {p.surface}, stop:1 {p.surface});
}}

/* 表格（照 shadcn 的 Table）：没有竖线、没有斑马纹，行之间一条细线，悬停变浅灰 */
QTableView, QTableWidget {{
    background: {p.surface}; border: 1px solid {p.border}; border-radius: 10px; gridline-color: {p.border};
    selection-background-color: {p.hover}; selection-color: {p.text};
}}
QTableView::item {{ padding: 0 8px; border: none; border-bottom: 1px solid {p.border}; }}
QTableView::item:hover {{ background: {p.subtle}; }}
QTableView::item:selected {{ background: {p.hover}; color: {p.text}; }}
QHeaderView {{ background: transparent; border: none; }}
QHeaderView::section {{
    background: {p.surface}; color: {p.text2}; font-weight: 500; padding: 10px 8px;
    border: none; border-bottom: 1px solid {p.border};
}}
QTableCornerButton::section {{ background: {p.surface}; border: none; }}
QListWidget {{ background: transparent; border: none; color: {p.text3}; font-size: {CAPTION}px; }}
QListWidget::item {{ padding: 3px 0; }}
QListWidget::item:selected {{ background: transparent; color: {p.text3}; }}
QTextBrowser {{ background: {p.surface}; border: 1px solid {p.border}; border-radius: 10px; padding: 16px; }}

QProgressBar {{
    background: {p.selected}; border: none; border-radius: 4px; min-height: 8px; max-height: 8px;
    color: transparent; text-align: center;
}}
QProgressBar::chunk {{
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #9DBDFF, stop:1 {p.highlight});
    border-radius: 4px;
}}

QScrollArea {{ background: transparent; border: none; }}
QScrollArea > QWidget#qt_scrollarea_viewport, QWidget#ScrollBody {{ background: transparent; }}
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: {p.border_strong}; border-radius: 3px; min-height: 32px; }}
QScrollBar::handle:horizontal {{ background: {p.border_strong}; border-radius: 3px; min-width: 32px; }}
QScrollBar::handle:hover {{ background: {p.text3}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}
QSplitter::handle {{ background: transparent; }}

QMenu {{ background: {p.surface}; border: 1px solid {p.border}; border-radius: 8px; padding: 4px; }}
QMenu::item {{ padding: 6px 12px; border-radius: 6px; color: {p.text}; }}
QMenu::item:selected {{ background: {p.hover}; }}
QMenu::separator {{ height: 1px; background: {p.border}; margin: 4px 0; }}

/* 自定义组件 */
QFrame#Tile {{ background: {p.surface}; border: 1px solid {p.border}; border-radius: 12px; }}
QFrame#Tile:hover {{ border-color: {p.border_strong}; }}
QFrame#Tile[checked="true"] {{ border-color: {p.accent}; }}
QFrame#Tile[dashed="true"] {{ background: transparent; border: 1px dashed {p.border_strong}; }}
QFrame#Tile[dashed="true"]:hover {{ background: {p.subtle}; border-color: {p.text3}; }}
QFrame#DropZone {{ background: {p.surface}; border: 1px dashed {p.border_strong}; border-radius: 12px; }}
QFrame#DropZone:hover {{ background: {p.subtle}; border-color: {p.text3}; }}
QFrame#DropZone[active="true"] {{ background: {p.subtle}; border: 1px solid {p.accent}; }}
QFrame#Panel {{ background: {p.surface}; border: 1px solid {p.border}; border-radius: 14px; }}
QFrame#PurposeCard {{ background: {p.surface}; border: 1px solid {p.border}; border-radius: 14px; }}
QFrame#PurposeCard:hover {{ border-color: {p.border_strong}; }}
QFrame#PurposeCard:focus {{ border-color: {p.ring}; }}
QFrame#PurposeCard[checked="true"] {{ border-color: {p.accent}; }}
QFrame#GoDot {{ background: {p.hover}; border: none; border-radius: 16px; }}
QFrame#GoDot[hot="true"] {{ background: {p.accent}; }}
"""


# ---------------------------------------------------------------------- 主题切换
def _prefs() -> QSettings:
    """界面偏好（主题）单独存一个小文件，和 config.yaml 分开。"""
    return QSettings(str(app_home() / "ui.ini"), QSettings.Format.IniFormat)


class ThemeManager(QObject):
    """当前主题。切换时重新设置调色板和样式表，并发出 changed 信号（需要自己画的组件据此刷新）。"""

    changed = Signal()

    def __init__(self):
        super().__init__()
        self.mode = "system"
        self.palette = LIGHT
        self._app: QApplication | None = None
        self._applying = False

    @property
    def is_dark(self) -> bool:
        return self.palette.is_dark

    def color(self, role: str) -> str:
        return getattr(self.palette, role)

    def install(self, app: QApplication) -> None:
        if self._app is app:
            return
        self._app = app
        mode = str(_prefs().value("theme", "light"))  # 默认浅色：演示、截图时和幻灯片一致
        self.mode = mode if mode in MODES else "light"
        app.styleHints().colorSchemeChanged.connect(self._system_changed)
        self.apply()

    def set_mode(self, mode: str) -> None:
        """mode：system（跟随系统）/ light / dark，会记住下次打开时用。"""
        if mode not in MODES:
            raise ValueError(mode)
        self.mode = mode
        _prefs().setValue("theme", mode)
        self.apply()

    def toggle(self) -> None:
        self.set_mode("light" if self.is_dark else "dark")

    def apply(self) -> None:
        if self._app is None or self._applying:
            return
        self._applying = True
        try:
            hints = self._app.styleHints()
            if self.mode == "system":
                hints.unsetColorScheme()
                dark = hints.colorScheme() == Qt.ColorScheme.Dark
            else:
                dark = self.mode == "dark"
                # 同时告诉系统，macOS 的窗口标题栏也会跟着变深 / 变浅
                hints.setColorScheme(Qt.ColorScheme.Dark if dark else Qt.ColorScheme.Light)
            self.palette = DARK if dark else LIGHT
            self._app.setPalette(qpalette(self.palette))
            self._app.setStyleSheet(stylesheet(self.palette))
        finally:
            self._applying = False
        for widget in self._app.allWidgets():
            widget.update()
        self.changed.emit()

    def _system_changed(self, scheme) -> None:
        if self.mode == "system" and (scheme == Qt.ColorScheme.Dark) != self.is_dark:
            self.apply()


theme = ThemeManager()
