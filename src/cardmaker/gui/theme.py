"""界面主题：浅色 / 深色两套配色、三级字号和全局样式表。界面上的颜色都从这里取，改风格只改这个文件。

设计约定
- 黑白灰为主。强调色（蓝）只用在主按钮和当前步骤，占画面很小的面积；
  勾选框、进度条、选中状态、链接都用中性色。
- 三级字号：标题 22、正文 14、说明 12（像素），全局用系统字体（中文苹方、英文 SF）。
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

TITLE, BODY, CAPTION = 22, 14, 12  # 三级字号（像素）

ICON_DIR = Path(__file__).parent / "assets" / "icons"
MODES = {"system": "跟随系统", "light": "浅色", "dark": "深色"}


@dataclass(frozen=True)
class Palette:
    name: str
    bg: str  # 窗口底色
    surface: str  # 卡片、输入框
    subtle: str  # 次级区域：文件信息、预览、代码编辑器
    hover: str  # 鼠标悬停
    selected: str  # 选中（中性灰）
    border: str  # 分隔线、卡片边框
    border_strong: str  # 输入框边框、虚线框
    text: str  # 主要文字
    text2: str  # 次要文字
    text3: str  # 说明、占位文字
    disabled: str
    accent: str  # 强调色：只给主按钮和当前步骤
    accent_hover: str
    accent_pressed: str
    on_accent: str
    accent_text: str

    @property
    def is_dark(self) -> bool:
        return self.name == "dark"


LIGHT = Palette(
    name="light", bg="#FFFFFF", surface="#FFFFFF", subtle="#FAFAFA", hover="#F5F5F5", selected="#EDEDED",
    border="#E8E8E8", border_strong="#D4D4D4", text="#171717", text2="#525252", text3="#737373",
    disabled="#A3A3A3", accent="#0F6CBD", accent_hover="#115EA3", accent_pressed="#0C3B5E",
    on_accent="#FFFFFF", accent_text="#0F6CBD")

DARK = Palette(
    name="dark", bg="#161616", surface="#1C1C1C", subtle="#1A1A1A", hover="#242424", selected="#2E2E2E",
    border="#2A2A2A", border_strong="#3D3D3D", text="#EDEDED", text2="#A3A3A3", text3="#8A8A8A",
    disabled="#5C5C5C", accent="#115EA3", accent_hover="#0F6CBD", accent_pressed="#0C3B5E",
    on_accent="#FFFFFF", accent_text="#479EF5")


def tinted_svg(name: str, color: str) -> bytes:
    """读取图标 SVG 并换成指定颜色（Fluent 图标本身不带颜色，给根节点加 fill 即可）。"""
    return (ICON_DIR / f"{name}.svg").read_bytes().replace(b"<svg ", f'<svg fill="{color}" '.encode(), 1)


def _write_assets(p: Palette) -> dict[str, str]:
    """样式表里的小图片（勾选框的对勾、下拉箭头）只能引用文件，按主题着色后写到临时目录。

    临时目录路径里没有空格，样式表的 url() 才认。
    """
    folder = Path(tempfile.gettempdir()) / f"cardmaker-ui-{os.getuid()}"
    folder.mkdir(exist_ok=True)
    assets = {}
    for key, name, color in (("check", "checkmark", p.bg), ("down", "chevron_down", p.text2),
                             ("up", "chevron_up", p.text2), ("down_disabled", "chevron_down", p.disabled)):
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
        QPalette.ColorRole.PlaceholderText: p.text3, QPalette.ColorRole.Highlight: p.selected,
        QPalette.ColorRole.HighlightedText: p.text, QPalette.ColorRole.ToolTipBase: p.text,
        QPalette.ColorRole.ToolTipText: p.bg, QPalette.ColorRole.Link: p.text,
        QPalette.ColorRole.LinkVisited: p.text, QPalette.ColorRole.Light: p.surface,
        QPalette.ColorRole.Midlight: p.hover, QPalette.ColorRole.Mid: p.border_strong,
        QPalette.ColorRole.Dark: p.border_strong, QPalette.ColorRole.Shadow: p.border_strong,
    }
    for role, color in roles.items():
        pal.setColor(role, QColor(color))
    for role in (QPalette.ColorRole.Text, QPalette.ColorRole.WindowText, QPalette.ColorRole.ButtonText):
        pal.setColor(QPalette.ColorGroup.Disabled, role, QColor(p.disabled))
    return pal


def stylesheet(p: Palette) -> str:
    a = _write_assets(p)
    return f"""
* {{ outline: none; }}
QWidget {{ color: {p.text}; }}
QMainWindow, QDialog, QMessageBox {{ background: {p.bg}; }}
#Page, #PageContent, #Header, #Footer {{ background: {p.bg}; }}
#Header {{ border-bottom: 1px solid {p.border}; }}
#Footer {{ border-top: 1px solid {p.border}; }}

QLabel {{ background: transparent; }}
QLabel[role="title"] {{ font-size: {TITLE}px; font-weight: 600; }}
QLabel[role="strong"] {{ font-weight: 600; }}
QLabel[role="secondary"] {{ color: {p.text2}; }}
QLabel[role="caption"] {{ font-size: {CAPTION}px; color: {p.text2}; }}
QLabel[role="meta"] {{ font-size: {CAPTION}px; color: {p.text3}; }}

QToolTip {{
    color: {p.bg}; background: {p.text}; border: none; padding: 6px 8px; border-radius: 6px;
    font-size: {CAPTION}px;
}}

/* 按钮：默认是次要按钮；variant=primary 主按钮（唯一用强调色的控件）；ghost 无边框；icon 纯图标 */
QPushButton {{
    background: {p.surface}; color: {p.text}; border: 1px solid {p.border_strong}; border-radius: 6px;
    padding: 5px 14px;
}}
QPushButton:hover {{ background: {p.hover}; }}
QPushButton:pressed, QPushButton:checked {{ background: {p.selected}; }}
QPushButton:disabled {{ color: {p.disabled}; border-color: {p.border}; background: {p.surface}; }}
QPushButton[variant="primary"] {{
    background: {p.accent}; color: {p.on_accent}; border: 1px solid {p.accent}; font-weight: 600; padding: 5px 20px;
}}
QPushButton[variant="primary"]:hover {{ background: {p.accent_hover}; border-color: {p.accent_hover}; }}
QPushButton[variant="primary"]:pressed {{ background: {p.accent_pressed}; border-color: {p.accent_pressed}; }}
QPushButton[variant="primary"]:disabled {{ background: {p.selected}; border-color: {p.selected}; color: {p.disabled}; }}
QPushButton[variant="ghost"] {{ background: transparent; border: 1px solid transparent; color: {p.text2}; padding: 5px 10px; }}
QPushButton[variant="ghost"]:hover {{ background: {p.hover}; color: {p.text}; }}
QPushButton[variant="ghost"]:pressed {{ background: {p.selected}; }}
QPushButton[variant="ghost"]:disabled {{ color: {p.disabled}; background: transparent; }}
QPushButton[variant="icon"] {{
    background: transparent; border: none; border-radius: 6px; padding: 0;
    min-width: 32px; max-width: 32px; min-height: 32px; max-height: 32px;
}}
QPushButton[variant="icon"]:hover {{ background: {p.hover}; }}
QPushButton[variant="icon"]:pressed {{ background: {p.selected}; }}
QPushButton::menu-indicator {{
    image: url({a["down"]}); width: 12px; height: 12px;
    subcontrol-origin: padding; subcontrol-position: center right; right: 6px;
}}
QPushButton[menu="true"] {{ padding-right: 26px; }}

/* 分段按钮（预览的正反方向） */
QPushButton[segment] {{
    background: {p.surface}; color: {p.text2}; border: 1px solid {p.border_strong}; border-radius: 0;
    padding: 3px 12px; font-size: {CAPTION}px;
}}
QPushButton[segment="first"] {{ border-top-left-radius: 6px; border-bottom-left-radius: 6px; }}
QPushButton[segment="last"] {{ border-top-right-radius: 6px; border-bottom-right-radius: 6px; border-left: none; }}
QPushButton[segment]:hover {{ color: {p.text}; }}
QPushButton[segment]:checked {{ background: {p.selected}; color: {p.text}; }}

/* 输入框 */
QLineEdit, QComboBox, QSpinBox, QPlainTextEdit, QTextEdit {{
    background: {p.surface}; color: {p.text}; border: 1px solid {p.border_strong}; border-radius: 6px;
    selection-background-color: {p.selected}; selection-color: {p.text};
}}
QLineEdit, QComboBox, QSpinBox {{ padding: 5px 10px; }}
QPlainTextEdit, QTextEdit {{ padding: 6px 8px; }}
QLineEdit:hover, QComboBox:hover, QSpinBox:hover, QPlainTextEdit:hover {{ border-color: {p.text3}; }}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QPlainTextEdit:focus, QTextEdit:focus {{ border-color: {p.text}; }}
QLineEdit:disabled, QComboBox:disabled, QSpinBox:disabled {{
    color: {p.disabled}; background: {p.subtle}; border-color: {p.border};
}}
QComboBox {{ padding-right: 28px; combobox-popup: 0; }}
QComboBox QLineEdit {{ border: none; padding: 0; margin: 0; background: transparent; }}
QComboBox::drop-down {{ border: none; width: 28px; subcontrol-origin: padding; subcontrol-position: center right; }}
QComboBox::down-arrow {{ image: url({a["down"]}); width: 12px; height: 12px; }}
QComboBox::down-arrow:disabled {{ image: url({a["down_disabled"]}); }}
QComboBox QAbstractItemView {{
    background: {p.surface}; border: 1px solid {p.border}; border-radius: 8px; padding: 4px;
    selection-background-color: {p.hover}; selection-color: {p.text}; outline: none;
}}
QSpinBox {{ padding-right: 26px; }}
QSpinBox::up-button, QSpinBox::down-button {{
    subcontrol-origin: border; width: 20px; border: none; background: transparent; margin-right: 3px;
}}
QSpinBox::up-button {{ subcontrol-position: top right; margin-top: 3px; }}
QSpinBox::down-button {{ subcontrol-position: bottom right; margin-bottom: 3px; }}
QSpinBox::up-button:hover, QSpinBox::down-button:hover {{ background: {p.hover}; border-radius: 4px; }}
QSpinBox::up-arrow {{ image: url({a["up"]}); width: 10px; height: 10px; }}
QSpinBox::down-arrow {{ image: url({a["down"]}); width: 10px; height: 10px; }}
QPlainTextEdit#Code {{ background: {p.subtle}; font-family: Menlo; font-size: 13px; }}

/* 勾选框、单选框：选中后是黑底白勾（深色主题反过来），不用强调色 */
QCheckBox, QRadioButton {{ spacing: 8px; background: transparent; }}
QCheckBox::indicator, QTableView::indicator {{
    width: 14px; height: 14px; border: 1px solid {p.border_strong}; border-radius: 4px; background: {p.surface};
}}
QCheckBox::indicator:hover, QTableView::indicator:hover {{ border-color: {p.text3}; }}
QCheckBox::indicator:checked, QTableView::indicator:checked {{
    background: {p.text}; border-color: {p.text}; image: url({a["check"]});
}}
QCheckBox::indicator:disabled {{ background: {p.subtle}; border-color: {p.border}; }}
QRadioButton::indicator {{
    width: 14px; height: 14px; border: 1px solid {p.border_strong}; border-radius: 8px; background: {p.surface};
}}
QRadioButton::indicator:hover {{ border-color: {p.text3}; }}
QRadioButton::indicator:checked {{ width: 6px; height: 6px; border: 5px solid {p.text}; background: {p.surface}; }}

/* 表格、列表 */
QTableView, QTableWidget {{
    background: {p.surface}; border: 1px solid {p.border}; border-radius: 8px; gridline-color: {p.border};
    selection-background-color: {p.selected}; selection-color: {p.text};
}}
QTableView::item {{ padding: 0 8px; border-bottom: 1px solid {p.border}; }}
QTableView::item:selected {{ background: {p.selected}; color: {p.text}; }}
QHeaderView {{ background: transparent; border: none; }}
QHeaderView::section {{
    background: {p.surface}; color: {p.text3}; font-size: {CAPTION}px; padding: 8px;
    border: none; border-bottom: 1px solid {p.border};
}}
QTableCornerButton::section {{ background: {p.surface}; border: none; }}
QListWidget {{ background: transparent; border: none; color: {p.text3}; font-size: {CAPTION}px; }}
QListWidget::item {{ padding: 3px 0; }}
QListWidget::item:selected {{ background: transparent; color: {p.text3}; }}
QTextBrowser {{ background: {p.subtle}; border: 1px solid {p.border}; border-radius: 10px; padding: 12px; }}

QProgressBar {{
    background: {p.border}; border: none; border-radius: 2px; min-height: 4px; max-height: 4px;
    color: transparent; text-align: center;
}}
QProgressBar::chunk {{ background: {p.text}; border-radius: 2px; }}

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
QMenu::item {{ padding: 6px 14px; border-radius: 4px; color: {p.text}; }}
QMenu::item:selected {{ background: {p.hover}; }}
QMenu::separator {{ height: 1px; background: {p.border}; margin: 4px 8px; }}

/* 自定义组件 */
QFrame#Tile {{ background: {p.surface}; border: 1px solid {p.border}; border-radius: 10px; }}
QFrame#Tile:hover {{ background: {p.hover}; border-color: {p.border_strong}; }}
QFrame#Tile[checked="true"] {{ background: {p.surface}; border-color: {p.text}; }}
QFrame#Tile[dashed="true"] {{ background: transparent; border: 1px dashed {p.border_strong}; }}
QFrame#Tile[dashed="true"]:hover {{ background: {p.hover}; border-color: {p.text3}; }}
QFrame#DropZone {{ background: {p.surface}; border: 1px dashed {p.border_strong}; border-radius: 10px; }}
QFrame#DropZone:hover {{ background: {p.hover}; border-color: {p.text3}; }}
QFrame#DropZone[active="true"] {{ background: {p.hover}; border: 1px solid {p.text}; }}
QFrame#Panel {{ background: {p.subtle}; border: 1px solid {p.border}; border-radius: 10px; }}
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
        mode = str(_prefs().value("theme", "system"))
        self.mode = mode if mode in MODES else "system"
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
