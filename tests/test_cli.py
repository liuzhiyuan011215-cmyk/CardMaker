import pytest

from cardmaker import cli
from cardmaker.config import load_settings, mask_key


def test_parse_pages():
    assert cli.parse_pages("1-3,5, 7") == [1, 2, 3, 5, 7]
    assert cli.parse_pages(None) is None


def test_parse_params():
    assert cli.parse_params(["level=TOPIK 4级", "ai_example=false"]) == {"level": "TOPIK 4级", "ai_example": "false"}
    with pytest.raises(SystemExit):
        cli.parse_params(["no-equals-sign"])


def test_set_key_saves_to_isolated_config_and_never_prints_it(monkeypatch, capsys, isolated_home):
    secret = "sk-test-0123456789abcdef"
    monkeypatch.setattr(cli.getpass, "getpass", lambda prompt: secret)
    assert cli.main(["config", "set-key"]) == 0
    assert load_settings().llm.api_key == secret
    assert (isolated_home / "config.yaml").stat().st_mode & 0o077 == 0  # 只有自己可读写

    cli.main(["config", "show"])
    out = capsys.readouterr().out
    assert secret not in out and mask_key(secret) in out


@pytest.mark.parametrize("raw,expected_key,has_note", [
    ("sk-abc123", "sk-abc123", False),
    ("  sk-abc123 \n", "sk-abc123", False),
    ("sk-abc123sk-abc123sk-abc1", "sk-abc123", True),  # 看不到输入，重复粘贴了三次
    ("\x1b[200~sk-abc123\x1b[201~", "sk-abc123", False),  # 终端的括号粘贴控制符
])
def test_clean_pasted_key(raw, expected_key, has_note):
    key, note = cli.clean_pasted_key(raw)
    assert key == expected_key and bool(note) == has_note


def test_preset_switch():
    assert cli.main(["config", "preset", "deepseek"]) == 0
    settings = load_settings()
    assert settings.llm.base_url == "https://api.deepseek.com" and settings.llm.extra_body == {}


def test_generate_without_key_explains(capsys, tmp_path):
    assert cli.main(["generate", str(tmp_path / "x.pdf"), "-t", "vocab_reading"]) == 1
    assert "config set-key" in capsys.readouterr().out
