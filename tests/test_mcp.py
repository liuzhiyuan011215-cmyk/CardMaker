"""大模型接口（MCP）测试：模拟 AI 助手发 JSON-RPC 消息。大模型、Anki 都用假的，不花 token。"""
import io
import json

from cardmaker.anki import ADDED, AnkiConnect
from cardmaker.mcp_server import CardMakerTools, Server, serve
from cardmaker.storage import TaskStore
from test_anki import FakeAnki
from test_resume import S1, S2, S3, PageFakeLLM, reply

TEST_DECK = "test::CardMaker 毕业设计"


def make_server(tmp_path, llm=None, anki=None):
    tools = CardMakerTools(store=TaskStore(tmp_path / "mcp.db"), llm_factory=(lambda: llm) if llm else None,
                           anki_factory=(lambda: AnkiConnect(transport=anki)) if anki else None)
    notifications = []
    return Server(tools, notifications.append), notifications


def call(server, name, **arguments):
    result = server.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                            "params": {"name": name, "arguments": arguments}})["result"]
    return result["content"][0]["text"], result["isError"]


def test_handshake_and_tool_list(tmp_path):
    server, _ = make_server(tmp_path)
    init = server.handle({"jsonrpc": "2.0", "id": 0, "method": "initialize",
                          "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t"}}})
    assert init["result"]["protocolVersion"] == "2025-06-18" and "tools" in init["result"]["capabilities"]
    assert server.handle({"jsonrpc": "2.0", "method": "notifications/initialized"}) is None  # 通知不用回
    tools = server.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})["result"]["tools"]
    assert [t["name"] for t in tools] == ["extract_text", "list_purposes", "generate_cards", "list_tasks",
                                          "get_cards", "export_apkg", "import_to_anki"]
    assert all(t["inputSchema"]["type"] == "object" and t["description"] for t in tools)
    assert server.handle({"jsonrpc": "2.0", "id": 3, "method": "resources/list"})["error"]["code"] == -32601


def test_only_ready_purposes_are_offered(tmp_path, make_pdf):
    server, _ = make_server(tmp_path)
    text, is_error = call(server, "list_purposes")
    assert not is_error and {p["purpose"] for p in json.loads(text)} == {"vocab_book", "vocab_reading"}
    text, is_error = call(server, "generate_cards", path=str(make_pdf([S1])), purpose="law_cloze")
    assert is_error and "不支持的用途" in text


def test_extract_text_spends_no_tokens(tmp_path, make_pdf):
    server, _ = make_server(tmp_path)
    text, is_error = call(server, "extract_text", path=str(make_pdf([S1, S2])), pages="2")
    assert not is_error and S2 in text and S1 not in text


def test_errors_are_reported_to_the_assistant(tmp_path):
    server, _ = make_server(tmp_path)
    assert call(server, "extract_text", path=str(tmp_path / "没有这个.pdf")) == (
        f"出错：找不到文件：{tmp_path / '没有这个.pdf'}", True)
    assert call(server, "get_cards", task_id=99)[1]
    assert call(server, "no_such_tool")[1]
    assert "参数不对" in call(server, "list_tasks", wrong=1)[0]


def test_generate_check_export_and_import(tmp_path, make_pdf):
    llm = PageFakeLLM({"PAGE1": reply(("확산되다", S1)), "PAGE2": reply(("방치되다", S2))})
    fake = FakeAnki()
    server, notifications = make_server(tmp_path, llm, fake)
    pdf = make_pdf([f"PAGE1 {S1} {S3}", f"PAGE2 {S2} {S3}"])
    result = server.handle({"jsonrpc": "2.0", "id": 5, "method": "tools/call", "params": {
        "name": "generate_cards", "arguments": {"path": str(pdf), "purpose": "vocab_reading", "level": "TOPIK 4级"},
        "_meta": {"progressToken": "p1"}}})["result"]
    text = result["content"][0]["text"]
    assert not result["isError"] and "확산되다" in text
    assert notifications and {n["params"]["progressToken"] for n in notifications} == {"p1"}  # 边做边报进度
    task_id = int(text.split("任务编号：")[1].split("\n")[0])

    cards = json.loads(call(server, "get_cards", task_id=task_id)[0])
    assert [c["Front"] for c in cards] == ["확산되다", "방치되다"]
    assert json.loads(call(server, "list_tasks")[0])[0]["任务编号"] == task_id

    text, is_error = call(server, "export_apkg", task_id=task_id, path=str(tmp_path))
    assert not is_error and len(list(tmp_path.glob("*.apkg"))) == 1

    text, is_error = call(server, "import_to_anki", task_id=task_id, deck=TEST_DECK, directions="forward")
    assert not is_error and TEST_DECK in text
    assert {n["deckName"] for n in fake.added} == {TEST_DECK}
    assert all(n["fields"]["CardForward"] == "y" and n["fields"]["CardReverse"] == "" for n in fake.added)
    assert {c["导入状态"] for c in json.loads(call(server, "get_cards", task_id=task_id)[0])} == {ADDED}


def test_serve_over_stdio(tmp_path):
    lines = [{"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2099-01-01"}},
             {"jsonrpc": "2.0", "method": "notifications/initialized"},
             {"jsonrpc": "2.0", "id": 2, "method": "ping"}]
    stdin = io.StringIO("".join(json.dumps(m) + "\n" for m in lines) + "不是 JSON\n")
    stdout = io.StringIO()
    serve(stdin, stdout, CardMakerTools(store=TaskStore(tmp_path / "mcp.db")))
    responses = [json.loads(line) for line in stdout.getvalue().splitlines()]
    assert [r.get("id") for r in responses] == [1, 2, None]
    assert responses[0]["result"]["protocolVersion"] == "2025-11-25"  # 不认识的新版本：回自己支持的最新版
    assert responses[2]["error"]["code"] == -32700
