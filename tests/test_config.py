from app.core import config


def test_get_hf_token_resolves_huggingface_token(monkeypatch):
    monkeypatch.delenv("HF_TOKEN", raising=False)
    monkeypatch.setenv("HUGGINGFACE_TOKEN", "test-token")

    assert config.get_hf_token() == "test-token"
