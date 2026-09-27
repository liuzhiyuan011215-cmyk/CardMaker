"""OpenAI 兼容接口的大模型客户端（百炼、DeepSeek 等只要改 base_url 和模型名）。"""
from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass
from typing import Protocol

from ..config import LLMSettings

log = logging.getLogger(__name__)


@dataclass
class Usage:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cached_tokens: int = 0  # 命中服务端缓存的输入 token（部分平台单价更低）
    calls: int = 0

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens

    def add(self, other: "Usage") -> None:
        self.prompt_tokens += other.prompt_tokens
        self.completion_tokens += other.completion_tokens
        self.cached_tokens += other.cached_tokens
        self.calls += other.calls


class FatalLLMError(RuntimeError):
    """重试也没用的错误（Key 不对、没有权限、模型名不存在），整个任务应立即停止。"""


def explain_error(exc: Exception) -> str:
    """把接口错误翻译成用户看得懂的话。"""
    import openai

    if isinstance(exc, openai.AuthenticationError):
        return "API Key 不正确或已失效，请在「设置」里重新粘贴。"
    if isinstance(exc, openai.PermissionDeniedError):
        return "这个 API Key 没有调用该模型的权限，请在平台控制台检查。"
    if isinstance(exc, openai.NotFoundError):
        return "找不到这个模型或接口地址，请在「设置」里检查模型名称和接口地址。"
    if isinstance(exc, openai.RateLimitError):
        return "请求太频繁，或免费额度已用完，请稍后再试或检查额度。"
    if isinstance(exc, openai.APIConnectionError):
        return "连不上大模型服务，请检查网络。"
    return f"{type(exc).__name__}: {exc}"


class ChatModel(Protocol):
    """生成流程只依赖这个接口，测试时可以换成假的模型。"""

    def chat(self, messages: list[dict]) -> tuple[str, Usage]: ...


class LLMClient:
    def __init__(self, base_url: str, api_key: str, model: str, *, json_mode: bool = True,
                 temperature: float = 0.3, timeout: float = 120, extra_body: dict | None = None,
                 max_retries: int = 2):
        from openai import OpenAI

        # SDK 自带重试：网络错误、429 限流、5xx 会自动指数退避重试 max_retries 次
        self._client = OpenAI(base_url=base_url, api_key=api_key, timeout=timeout, max_retries=max_retries)
        self.model = model
        self.json_mode = json_mode
        self.temperature = temperature
        self.extra_body = extra_body or {}

    @classmethod
    def from_settings(cls, settings: LLMSettings) -> "LLMClient":
        return cls(settings.base_url, settings.api_key, settings.model, json_mode=settings.json_mode,
                   temperature=settings.temperature, timeout=settings.timeout,
                   extra_body=settings.extra_body)

    def chat(self, messages: list[dict]) -> tuple[str, Usage]:
        kwargs: dict = {"model": self.model, "messages": messages, "temperature": self.temperature}
        if self.json_mode:
            kwargs["response_format"] = {"type": "json_object"}
        if self.extra_body:
            kwargs["extra_body"] = self.extra_body
        import openai

        try:
            resp = self._client.chat.completions.create(**kwargs)
        except (openai.AuthenticationError, openai.PermissionDeniedError, openai.NotFoundError) as exc:
            raise FatalLLMError(explain_error(exc)) from exc
        content = resp.choices[0].message.content or ""
        usage = Usage(calls=1)
        if resp.usage:
            usage.prompt_tokens = resp.usage.prompt_tokens or 0
            usage.completion_tokens = resp.usage.completion_tokens or 0
            details = getattr(resp.usage, "prompt_tokens_details", None)
            usage.cached_tokens = getattr(details, "cached_tokens", 0) or 0
        log.debug("LLM 返回 %d 字符，token %d+%d", len(content), usage.prompt_tokens, usage.completion_tokens)
        return content, usage


def check_connection(settings: LLMSettings) -> str:
    """发一条极短的请求，确认 Key、地址、模型名、JSON 模式都可用（约几十个 token）。失败时抛异常。"""
    client = LLMClient.from_settings(settings.model_copy(update={"timeout": min(settings.timeout, 30)}))
    start = time.monotonic()
    content, usage = client.chat([{"role": "system", "content": "你只输出 json。"},
                                  {"role": "user", "content": '请原样输出：{"ok": true}'}])
    parse_json_object(content)
    return (f"连接成功：{settings.model}，耗时 {time.monotonic() - start:.1f} 秒，"
            f"用了 {usage.total_tokens} 个 token")


_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.S)


def parse_json_object(text: str) -> dict:
    """从模型输出中取出 JSON 对象。兼容 ```json 代码块、前后多余文字等常见情况。"""
    text = text.strip()
    fenced = _FENCE.search(text)
    if fenced:
        text = fenced.group(1).strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start == -1 or end <= start:
            raise ValueError("输出中没有找到 JSON 对象") from None
        try:
            data = json.loads(text[start:end + 1])
        except json.JSONDecodeError as exc:
            raise ValueError(f"JSON 格式错误：{exc.msg}（第 {exc.lineno} 行）") from None
    if not isinstance(data, dict):
        raise ValueError("顶层必须是 JSON 对象（{...}）")
    return data
