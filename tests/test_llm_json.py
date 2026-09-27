import pytest

from cardmaker.llm.client import Usage, parse_json_object


@pytest.mark.parametrize("text", [
    '{"items": []}',
    '```json\n{"items": []}\n```',
    '好的，结果如下：\n{"items": []}\n希望有帮助',
    '  \n```\n{"items": []}\n```  ',
])
def test_parse_json_object_variants(text):
    assert parse_json_object(text) == {"items": []}


@pytest.mark.parametrize("text", ["", "没有 JSON", "[1, 2, 3]", '{"items": [}'])
def test_parse_json_object_errors(text):
    with pytest.raises(ValueError):
        parse_json_object(text)


def test_usage_add():
    total = Usage()
    total.add(Usage(prompt_tokens=10, completion_tokens=5, cached_tokens=2, calls=1))
    total.add(Usage(prompt_tokens=1, completion_tokens=1, calls=1))
    assert (total.prompt_tokens, total.completion_tokens, total.cached_tokens, total.calls) == (11, 6, 2, 2)
    assert total.total_tokens == 17
