"""AnkiConnect 客户端：通过 Anki 的 AnkiConnect 插件（代码 2055492159）读写牌组。

网络请求由 transport 负责，测试时可以换成假的，不需要真的打开 Anki。
"""
from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from collections.abc import Callable
from typing import Any

log = logging.getLogger(__name__)

DEFAULT_URL = "http://127.0.0.1:8765"
API_VERSION = 6


class AnkiConnectError(Exception):
    """AnkiConnect 返回了错误。"""


class AnkiUnavailable(AnkiConnectError):
    """连不上 AnkiConnect：Anki 没打开，或者没装 AnkiConnect 插件。"""


Transport = Callable[[str, dict, float], dict]


def http_transport(url: str, payload: dict, timeout: float) -> dict:
    data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(url, data, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.load(response)
    except (urllib.error.URLError, ConnectionError, TimeoutError, OSError) as exc:
        raise AnkiUnavailable(f"连不上 Anki（{url}）：请确认 Anki 已打开并安装了 AnkiConnect 插件") from exc


class AnkiConnect:
    def __init__(self, url: str = DEFAULT_URL, timeout: float = 15, transport: Transport = http_transport):
        self.url, self.timeout, self.transport = url, timeout, transport

    def invoke(self, action: str, **params: Any) -> Any:
        response = self.transport(self.url, {"action": action, "version": API_VERSION, "params": params}, self.timeout)
        if not isinstance(response, dict) or "error" not in response or "result" not in response:
            raise AnkiConnectError(f"AnkiConnect 返回的格式不对：{response!r}")
        if response["error"]:
            raise AnkiConnectError(str(response["error"]))
        log.debug("AnkiConnect %s 成功", action)
        return response["result"]

    # ------------------------------------------------------------------ 查询
    def version(self) -> int:
        return int(self.invoke("version"))

    def is_available(self) -> bool:
        try:
            return self.version() >= API_VERSION
        except AnkiConnectError:
            return False

    def deck_names(self) -> list[str]:
        return self.invoke("deckNames")

    def model_names(self) -> list[str]:
        return self.invoke("modelNames")

    def model_field_names(self, model: str) -> list[str]:
        return self.invoke("modelFieldNames", modelName=model)

    def can_add_notes(self, notes: list[dict]) -> list[dict]:
        """逐条检查能否添加，返回 [{"canAdd": bool, "error": str | None}, ...]。"""
        try:
            return self.invoke("canAddNotesWithErrorDetail", notes=notes)
        except AnkiConnectError as exc:
            if "unsupported action" not in str(exc):
                raise
            # 旧版 AnkiConnect 没有这个接口，退回到只返回 True/False 的版本
            return [{"canAdd": ok, "error": None if ok else "cannot create note (duplicate or empty)"}
                    for ok in self.invoke("canAddNotes", notes=notes)]

    def gui_browse(self, query: str) -> None:
        """在 Anki 里打开浏览窗口并搜索（只打开窗口，不改任何数据）。"""
        self.invoke("guiBrowse", query=query)

    # ------------------------------------------------------------------ 写入（只新增，从不修改或删除已有内容）
    def create_deck(self, name: str) -> int:
        return self.invoke("createDeck", deck=name)  # 已存在时直接返回它的 id

    def create_model(self, name: str, fields: list[str], templates: list[dict], css: str,
                     is_cloze: bool = False) -> dict:
        return self.invoke("createModel", modelName=name, inOrderFields=fields, css=css, isCloze=is_cloze,
                           cardTemplates=templates)

    def model_templates(self, name: str) -> dict[str, dict[str, str]]:
        """{模板名: {"Front": 正面, "Back": 背面}}。"""
        return self.invoke("modelTemplates", modelName=name)

    def update_model_templates(self, name: str, templates: dict[str, dict[str, str]]) -> None:
        self.invoke("updateModelTemplates", model={"name": name, "templates": templates})

    def store_media_file(self, path) -> str:
        """把本地音频文件放进 Anki 的媒体文件夹（同名文件会被覆盖，文件名带内容哈希，所以不会误覆盖）。"""
        return self.invoke("storeMediaFile", filename=path.name, path=str(path))

    def add_notes(self, notes: list[dict]) -> list[int | None]:
        """批量添加；如果整批被拒绝（新版 AnkiConnect 有一条失败就整体报错），改为逐条添加。"""
        try:
            return self.invoke("addNotes", notes=notes)
        except AnkiConnectError:
            results: list[int | None] = []
            for note in notes:
                try:
                    results.append(self.invoke("addNote", note=note))
                except AnkiConnectError as exc:
                    log.info("添加失败：%s", exc)
                    results.append(None)
            return results
