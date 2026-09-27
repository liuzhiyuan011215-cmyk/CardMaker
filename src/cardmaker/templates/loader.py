"""领域模板 = 提示词 + 输出 JSON 结构 + 卡片字段映射，每个模板是一个独立的 YAML 文件。

- 输出结构在 YAML 里声明，运行时据此生成 pydantic 模型，用来校验模型返回的 JSON；
- 提示词和卡片字段映射用 Jinja2 渲染，可以按用户勾选的选项增减内容；
- 内置模板在 templates/builtin/，用户自己的模板放在 用户数据目录/templates/，同名 id 时用户的优先。
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, Any, Literal

import yaml
from jinja2 import Environment, StrictUndefined, TemplateError as JinjaError
from markupsafe import Markup, escape
from pydantic import (BaseModel, ConfigDict, Field, StringConstraints, ValidationError,
                      ValidationInfo, create_model, field_validator)

from ..config import app_home
from ..llm.client import parse_json_object

BUILTIN_DIR = Path(__file__).parent / "builtin"


class TemplateError(Exception):
    """模板文件本身有问题（YAML 写错、变量名写错等）。"""


class OutputError(ValueError):
    """模型输出不符合模板要求，生成流程会据此重试。"""


# ---------------------------------------------------------------------- YAML 结构
class ParamSpec(BaseModel):
    label: str
    type: Literal["str", "int", "bool"] = "str"
    default: Any = ""
    description: str = ""
    choices: list[str] | None = None  # 界面上显示成下拉框（仍可手动输入）


class FieldSpec(BaseModel):
    type: Literal["str", "int", "bool", "list"] = "str"
    label: str = ""  # 预览表格的列名
    required: bool = False
    description: str = ""
    pattern: str | None = None  # 必须匹配的正则，例如填空卡必须含 {{c1::…}}
    min_items: int = 0  # list 类型至少几项，例如选择题至少 3 个干扰项
    enum: list[str] | None = None
    when: str | None = None  # 只有这个参数为真时才要求模型输出该字段（省 token）


class OutputSpec(BaseModel):
    list_key: str = "items"
    fields: dict[str, FieldSpec]
    grounded: list[str] = Field(default_factory=list)  # 这些字段的内容必须能在原文中找到
    cloze_grounded: list[str] = Field(default_factory=list)  # 这些字段里 {{c1::答案}} 的答案必须能在原文中找到
    mcq: bool = False  # 选择题：程序把正确答案和干扰项打乱，生成 option_a…option_d 和 answer


class TemplateSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    icon: str = "🗂️"  # 界面上模板卡片的图标
    order: int = 100  # 界面上的排列顺序，小的在前
    description: str = ""
    group: str = ""  # 用途分组（第一步的一张卡片），为空时等于 id
    group_name: str = ""  # 分组显示名，为空时用 name
    variant: str = ""  # 同一用途下的卡片类型，例如「填空卡」「选择题」
    source: Literal["document", "notes"] = "document"  # 资料类型：普通文档（按页）或读书笔记（按条）
    note_type: str = "vocab"
    dedup_key: str | None = None
    tags: list[str] = Field(default_factory=list)
    params: dict[str, ParamSpec] = Field(default_factory=dict)
    system_prompt: str
    user_prompt: str
    output: OutputSpec
    card_fields: dict[str, str]


# ---------------------------------------------------------------------- Jinja2 环境
_WORD_END = re.compile(r"[\s.,!?;:()\[\]\"'“”‘’。，！？；：（）「」]")


def find_term(text: str, term: str) -> tuple[int, int]:
    """在 text 中找 term，返回 (起点, 终点)，找不到返回 (-1, -1)。

    例句里的词往往是变形后的样子：韩语 방치되다 → 방치되어，英语 alleviate → alleviating。
    所以依次尝试：原样 → 韩语词干（去掉末尾的"다"）→ 英语前缀；找到后延伸到整个词（到空格或标点为止）。
    """
    term = (term or "").strip()
    if not term:
        return -1, -1
    candidates = [term]
    if len(term) > 2 and term.endswith("다"):
        candidates.append(term[:-1])
    if term.isascii() and len(term) > 4:
        candidates.append(term[:-1])
    lowered = text.lower()
    for cand in candidates:
        start = lowered.find(cand.lower())
        if start != -1:
            end = start + len(cand)
            # 用词干找到的、或英语词，延伸到词尾（alleviate → alleviated）；
            # 韩语名词原样找到时不延伸，免得把后面的助词也加粗
            if cand != term or term.isascii():
                stop = _WORD_END.search(text, end)
                end = stop.start() if stop else len(text)
            return start, end
    return -1, -1


def highlight(text: Any, term: Any) -> Markup:
    """把 text 中第一次出现的 term 加粗（用于在原句、例句里标出目标词）。"""
    text = "" if text is None else str(text)
    start, end = find_term(text, str(term or ""))
    if start == -1:
        return Markup(escape(text))
    return Markup(f"{escape(text[:start])}<b>{escape(text[start:end])}</b>{escape(text[end:])}")


def code_block(text: Any) -> Markup:
    """代码片段：保留缩进和换行（填空标记 {{c1::…}} 不受影响）。"""
    return Markup(f"<pre><code>{escape(text)}</code></pre>") if text else Markup("")


_PROMPT_ENV = Environment(undefined=StrictUndefined, trim_blocks=True, lstrip_blocks=True)
_CARD_ENV = Environment(autoescape=True, trim_blocks=True, lstrip_blocks=True)
_CARD_ENV.filters["highlight"] = highlight
_CARD_ENV.filters["code_block"] = code_block

_PY_TYPES = {"str": str, "int": int, "bool": bool, "list": list[str]}
_DEFAULTS = {"str": "", "int": 0, "bool": False}
_TRUE = {"true", "1", "yes", "y", "on", "是", "开"}


def _coerce_param(spec: ParamSpec, value: Any) -> Any:
    if spec.type == "bool":
        return value if isinstance(value, bool) else str(value).strip().lower() in _TRUE
    if spec.type == "int":
        return int(value)
    return "" if value is None else str(value)


def _build_item_model(template_id: str, fields: dict[str, FieldSpec]) -> type[BaseModel]:
    definitions: dict[str, Any] = {}
    enum_maps = {name: {v.lower(): v for v in f.enum} for name, f in fields.items() if f.enum}
    for name, f in fields.items():
        py_type: Any = Literal[tuple(f.enum)] if f.enum else _PY_TYPES[f.type]
        if f.type == "list" and f.min_items:
            py_type = Annotated[list[str], Field(min_length=f.min_items)]
        if f.required:
            if f.type == "str" and not f.enum:
                py_type = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, pattern=f.pattern)]
            definitions[name] = (py_type, Field(..., description=f.description))
        elif f.type == "list":
            definitions[name] = (py_type, Field(default_factory=list, description=f.description))
        else:
            default = f.enum[0] if f.enum else _DEFAULTS[f.type]
            definitions[name] = (py_type, Field(default=default, description=f.description))

    def normalize(cls, value, info: ValidationInfo):
        """模型常见的小毛病在这里修正：null、数字写成数字、枚举大小写不一致。"""
        field = cls.model_fields[info.field_name]
        if value is None and not field.is_required():
            return field.get_default(call_default_factory=True)
        spec = fields[info.field_name]
        if spec.type == "str" and isinstance(value, (int, float)) and not isinstance(value, bool):
            return str(value)
        if info.field_name in enum_maps and isinstance(value, str):
            return enum_maps[info.field_name].get(value.strip().lower(), value)
        return value

    return create_model(
        f"Item_{template_id}",
        __config__=ConfigDict(extra="ignore", str_strip_whitespace=True),
        __validators__={"_normalize": field_validator("*", mode="before")(normalize)},
        **definitions,
    )


def _brief_errors(exc: ValidationError) -> str:
    return "，".join(f"{'.'.join(str(p) for p in err['loc'])}: {err['msg']}" for err in exc.errors()[:3])


# ---------------------------------------------------------------------- 模板对象
class Template:
    def __init__(self, spec: TemplateSpec, source_path: Path):
        self.spec = spec
        self.source_path = source_path
        self.item_model = _build_item_model(spec.id, spec.output.fields)
        try:
            self._system = _PROMPT_ENV.from_string(spec.system_prompt)
            self._user = _PROMPT_ENV.from_string(spec.user_prompt)
            self._cards = {name: _CARD_ENV.from_string(src) for name, src in spec.card_fields.items()}
        except JinjaError as exc:
            raise TemplateError(f"模板 {spec.id} 语法错误：{exc}") from exc

    # 便捷属性
    id = property(lambda self: self.spec.id)
    name = property(lambda self: self.spec.name)
    icon = property(lambda self: self.spec.icon)
    group = property(lambda self: self.spec.group or self.spec.id)
    group_name = property(lambda self: self.spec.group_name or self.spec.name)
    variant = property(lambda self: self.spec.variant or self.spec.name)
    source = property(lambda self: self.spec.source)
    note_type = property(lambda self: self.spec.note_type)
    description = property(lambda self: self.spec.description)
    dedup_key = property(lambda self: self.spec.dedup_key)
    params = property(lambda self: self.spec.params)
    fields = property(lambda self: self.spec.output.fields)
    grounded = property(lambda self: self.spec.output.grounded)
    cloze_grounded = property(lambda self: self.spec.output.cloze_grounded)
    is_mcq = property(lambda self: self.spec.output.mcq)

    def resolve_params(self, given: dict[str, Any]) -> dict[str, Any]:
        """用户给的参数 + 模板默认值，并转换成声明的类型。"""
        resolved = {}
        for name, spec in self.spec.params.items():
            try:
                resolved[name] = _coerce_param(spec, given.get(name, spec.default))
            except ValueError as exc:
                raise TemplateError(f"参数 {name}（{spec.label}）的值不合法：{given.get(name)}") from exc
        unknown = set(given) - set(resolved)
        resolved.update({k: given[k] for k in unknown})  # 未声明的参数也传给提示词，方便自定义
        return resolved

    def active_fields(self, params: dict[str, Any]) -> dict[str, FieldSpec]:
        return {n: f for n, f in self.spec.output.fields.items() if not f.when or params.get(f.when)}

    def output_format(self, params: dict[str, Any]) -> str:
        key = self.spec.output.list_key
        lines = [f'只输出一个 json 对象，格式为 {{"{key}": [ {{...}}, ... ]}}；没有合适内容时输出 {{"{key}": []}}。',
                 "数组中每个元素包含以下字段："]
        for name, f in self.active_fields(params).items():
            kind = f"取值为 {' / '.join(f.enum)}" if f.enum else {"str": "字符串", "int": "整数",
                                                                "bool": "true/false", "list": "字符串数组"}[f.type]
            need = "必填" if f.required else "可为空"
            lines.append(f'- "{name}"（{need}，{kind}）：{f.description}')
        return "\n".join(lines)

    def build_messages(self, variables: dict[str, Any]) -> list[dict]:
        variables = {**variables, "output_format": self.output_format(variables)}
        try:
            system, user = self._system.render(**variables), self._user.render(**variables)
        except JinjaError as exc:
            raise TemplateError(f"模板 {self.id} 渲染失败：{exc}") from exc
        return [{"role": "system", "content": system.strip()}, {"role": "user", "content": user.strip()}]

    def parse_output(self, content: str) -> tuple[list[dict], list[str]]:
        """解析并校验模型输出，返回（合格条目, 不合格条目的错误说明）。

        整体不是 JSON、缺少数组、或者一半以上条目不合格 → 抛 OutputError 让上层重试；
        只有个别条目不合格 → 丢弃这几条，保留其余，避免为一两条浪费一次调用。
        """
        try:
            data = parse_json_object(content)
        except ValueError as exc:
            raise OutputError(str(exc)) from None
        key = self.spec.output.list_key
        items = data.get(key)
        if items is None:
            lists = [v for v in data.values() if isinstance(v, list)]
            if len(lists) != 1:
                raise OutputError(f'缺少 "{key}" 数组')
            items = lists[0]
        if not isinstance(items, list):
            raise OutputError(f'"{key}" 必须是数组')

        valid, errors = [], []
        for i, raw in enumerate(items, 1):
            if not isinstance(raw, dict):
                errors.append(f"第 {i} 项不是对象")
                continue
            try:
                valid.append(self.item_model.model_validate(raw).model_dump())
            except ValidationError as exc:
                errors.append(f"第 {i} 项 {_brief_errors(exc)}")
        if errors and len(errors) > len(valid):
            raise OutputError("；".join(errors[:5]))
        return valid, errors

    def render_card_fields(self, item: dict[str, Any], context: dict[str, Any]) -> dict[str, str]:
        values = {**context, **item}
        return {name: tpl.render(**values).strip() for name, tpl in self._cards.items()}


# ---------------------------------------------------------------------- 加载
def user_template_dir() -> Path:
    return app_home() / "templates"


def load_template_file(path: Path) -> Template:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        spec = TemplateSpec.model_validate(data)
    except yaml.YAMLError as exc:
        raise TemplateError(f"{path.name} 不是合法的 YAML：{exc}") from exc
    except ValidationError as exc:
        raise TemplateError(f"{path.name} 结构不对：{_brief_errors(exc)}") from exc
    return Template(spec, path)


def list_templates() -> list[Template]:
    found: dict[str, Template] = {}
    for folder in (BUILTIN_DIR, user_template_dir()):
        if folder.is_dir():
            for path in sorted(folder.glob("*.yaml")):
                tpl = load_template_file(path)
                found[tpl.id] = tpl  # 用户目录后加载，同名时覆盖内置
    return sorted(found.values(), key=lambda t: (t.spec.order, t.id))


@dataclass
class TemplateGroup:
    """第一步里的一张「用途」卡片，可能包含多种卡片类型（例如填空卡、选择题）。"""
    id: str
    name: str
    icon: str
    description: str
    variants: list[Template]


def list_groups() -> list[TemplateGroup]:
    groups: dict[str, TemplateGroup] = {}
    for tpl in list_templates():  # 已按 order 排好
        if tpl.group not in groups:
            groups[tpl.group] = TemplateGroup(tpl.group, tpl.group_name, tpl.icon, tpl.description, [])
        groups[tpl.group].variants.append(tpl)
    return list(groups.values())


def load_template(id_or_path: str) -> Template:
    path = Path(id_or_path)
    if path.suffix in (".yaml", ".yml") and path.exists():
        return load_template_file(path)
    for tpl in list_templates():
        if tpl.id == id_or_path:
            return tpl
    raise TemplateError(f"找不到模板：{id_or_path}（可用：{', '.join(t.id for t in list_templates())}）")
