"""界面样式（Qt 样式表）。颜色集中在这里，改主题只改这个文件。"""

PRIMARY = "#2563eb"
PRIMARY_DARK = "#1d4ed8"
PRIMARY_SOFT = "#eef4ff"
TEXT = "#1f2328"
MUTED = "#667085"
BORDER = "#e4e7ec"
BACKGROUND = "#f6f7f9"

STYLESHEET = f"""
QWidget {{ color: {TEXT}; font-size: 14px; }}
QMainWindow, QDialog, #Page {{ background: {BACKGROUND}; }}

#Header {{ background: white; border-bottom: 1px solid {BORDER}; }}
#AppTitle {{ font-size: 20px; font-weight: 700; }}
#AppSubtitle, #Hint {{ color: {MUTED}; }}
#PageTitle {{ font-size: 22px; font-weight: 700; }}
#Big {{ font-size: 17px; }}

QPushButton {{
    padding: 8px 16px; border-radius: 8px; border: 1px solid #d0d5dd; background: white;
}}
QPushButton:hover {{ background: #f2f4f7; }}
QPushButton:disabled {{ color: #98a2b3; background: #f2f4f7; }}
QPushButton#Primary {{
    background: {PRIMARY}; color: white; border: none; font-weight: 600; padding: 10px 26px; font-size: 15px;
}}
QPushButton#Primary:hover {{ background: {PRIMARY_DARK}; }}
QPushButton#Primary:disabled {{ background: #a8c1f7; color: white; }}
QPushButton#Link {{ border: none; background: transparent; color: {PRIMARY}; padding: 4px; }}
QPushButton#Link:hover {{ text-decoration: underline; }}

#DropZone {{ border: 2px dashed #b8c2d3; border-radius: 18px; background: white; }}
#DropZone[active="true"] {{ border-color: {PRIMARY}; background: {PRIMARY_SOFT}; }}

#Tile {{ border: 2px solid {BORDER}; border-radius: 14px; background: white; }}
#Tile:hover {{ border-color: #b8c2d3; }}
#Tile[checked="true"] {{ border-color: {PRIMARY}; background: {PRIMARY_SOFT}; }}
#TileTitle {{ font-size: 16px; font-weight: 700; }}
#TileText {{ color: {MUTED}; }}

#Panel {{ background: white; border: 1px solid {BORDER}; border-radius: 12px; }}

#Step {{ color: #98a2b3; font-size: 14px; }}
#Step[state="current"] {{ color: {PRIMARY}; font-weight: 700; }}
#Step[state="done"] {{ color: {TEXT}; }}
#StepLine {{ background: {BORDER}; }}

QProgressBar {{
    border: none; background: {BORDER}; border-radius: 7px; min-height: 14px; max-height: 14px; text-align: center;
    color: transparent;
}}
QProgressBar::chunk {{ background: {PRIMARY}; border-radius: 7px; }}

QLineEdit, QPlainTextEdit {{
    padding: 6px 8px; border: 1px solid #d0d5dd; border-radius: 8px; background: white;
}}
QLineEdit:focus, QPlainTextEdit:focus {{ border-color: {PRIMARY}; }}
QComboBox, QSpinBox {{ min-height: 30px; min-width: 90px; }}

QTableView {{
    background: white; border: 1px solid {BORDER}; border-radius: 10px; gridline-color: #eef0f3;
    selection-background-color: #dbe7ff; selection-color: {TEXT};
}}
QHeaderView::section {{ background: #f2f4f7; border: none; padding: 6px; font-weight: 600; }}
QTextBrowser, QListWidget {{ background: white; border: 1px solid {BORDER}; border-radius: 10px; }}
"""
