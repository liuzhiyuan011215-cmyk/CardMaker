"""模板查看与编辑：每个模板 = 提示词 + 输出 JSON 结构 + 卡片字段映射，保存在一个 YAML 文件里。

内置模板不会被改动：保存时存到用户自己的模板文件夹，同名（同 id）的会覆盖内置模板；「恢复默认」删掉用户那份即可。
"""
from __future__ import annotations

from pathlib import Path

import yaml
from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices, QFont
from PySide6.QtWidgets import QComboBox, QDialog, QHBoxLayout, QMessageBox, QPlainTextEdit, QPushButton, QVBoxLayout

from ..i18n import tr
from ..templates.loader import Template, TemplateError, TemplateSpec, list_templates, user_template_dir
from .widgets import label


class TemplateEditorDialog(QDialog):
    def __init__(self, parent=None, template: Template | None = None):
        super().__init__(parent)
        self.setWindowTitle(tr("查看 / 编辑模板"))
        self.resize(900, 680)
        self.templates: list[Template] = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 16, 20, 16)
        top = QHBoxLayout()
        top.addWidget(label("模板："))
        self.picker = QComboBox()
        self.picker.currentIndexChanged.connect(self._show_current)
        top.addWidget(self.picker, 1)
        layout.addLayout(top)
        layout.addWidget(label("每个模板 = 提示词（system_prompt / user_prompt）+ 输出结构（output）+ 卡片字段映射（card_fields）。"
                               "内置模板不会被改动：保存后存到你自己的模板文件夹，同名的会覆盖内置模板。", "Hint", wrap=True))
        self.editor = QPlainTextEdit()
        font = QFont("Menlo")
        font.setStyleHint(QFont.StyleHint.Monospace)
        font.setPointSize(13)
        self.editor.setFont(font)
        self.editor.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        layout.addWidget(self.editor, 1)
        self.status = label("", "Hint", wrap=True)
        layout.addWidget(self.status)

        buttons = QHBoxLayout()
        self.reset_button = QPushButton(tr("恢复默认"))
        self.reset_button.clicked.connect(self.reset)
        folder = QPushButton(tr("打开模板文件夹"))
        folder.clicked.connect(self.open_folder)
        buttons.addWidget(self.reset_button)
        buttons.addWidget(folder)
        buttons.addStretch()
        close = QPushButton(tr("关闭"))
        close.clicked.connect(self.reject)
        save = QPushButton(tr("保存为我的模板"))
        save.setObjectName("Primary")
        save.clicked.connect(self.save)
        buttons.addWidget(close)
        buttons.addWidget(save)
        layout.addLayout(buttons)
        self._reload(select_id=template.id if template else None)

    # ------------------------------------------------------------------
    def _reload(self, select_id: str | None = None) -> None:
        self.templates = list_templates()
        self.picker.blockSignals(True)
        self.picker.clear()
        for tpl in self.templates:
            mine = "（我的）" if self._is_user(tpl) else ""
            self.picker.addItem(f"{tpl.icon} {tr(tpl.name)}{tr(mine)}", tpl.id)
        self.picker.blockSignals(False)
        index = max(0, self.picker.findData(select_id)) if select_id else 0
        self.picker.setCurrentIndex(index)
        self._show_current()

    @staticmethod
    def _is_user(tpl: Template) -> bool:
        return tpl.source_path.parent == user_template_dir()

    def current(self) -> Template | None:
        index = self.picker.currentIndex()
        return self.templates[index] if 0 <= index < len(self.templates) else None

    def _show_current(self) -> None:
        tpl = self.current()
        if not tpl:
            return
        self.editor.setPlainText(tpl.source_path.read_text(encoding="utf-8"))
        self.reset_button.setEnabled(self._is_user(tpl))
        self.status.setText(tr(f"文件：{tpl.source_path}"))

    def validate(self, text: str) -> TemplateSpec:
        """检查 YAML 和模板语法，有错就抛出 TemplateError（说明哪里不对）。"""
        try:
            data = yaml.safe_load(text)
            spec = TemplateSpec.model_validate(data)
        except yaml.YAMLError as exc:
            raise TemplateError(f"YAML 格式不对：{exc}") from exc
        except Exception as exc:
            raise TemplateError(f"模板结构不对：{exc}") from exc
        Template(spec, Path("检查用.yaml"))  # 顺便检查 Jinja2 语法
        return spec

    def save(self) -> None:
        text = self.editor.toPlainText()
        try:
            spec = self.validate(text)
        except TemplateError as exc:
            QMessageBox.warning(self, tr("模板有错误"), tr(str(exc)))
            return
        folder = user_template_dir()
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"{spec.id}.yaml"
        path.write_text(text, encoding="utf-8")
        QMessageBox.information(self, tr("已保存"), tr(f"已保存到：\n{path}\n\n之后生成卡片会使用你修改后的模板。"))
        self.accept()

    def reset(self) -> None:
        tpl = self.current()
        if not tpl or not self._is_user(tpl):
            return
        answer = QMessageBox.question(self, tr("恢复默认"), tr(f"删除你修改过的「{tpl.name}」，恢复成内置模板？"))
        if answer == QMessageBox.StandardButton.Yes:
            tpl.source_path.unlink()  # 只删用户自己保存的那份，内置模板不受影响
            self._reload(select_id=tpl.id)
            self.status.setText(tr("已恢复默认。"))

    def open_folder(self) -> None:
        folder = user_template_dir()
        folder.mkdir(parents=True, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))
