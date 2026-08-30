import pytest

from vistarium.model_client import ConfigError, _wopr_base_url


def test_raises_clear_error_when_unset(monkeypatch):
    monkeypatch.delenv("WOPR_BASE_URL", raising=False)
    with pytest.raises(ConfigError, match="WOPR_BASE_URL"):
        _wopr_base_url()


def test_returns_configured_url(monkeypatch):
    monkeypatch.setenv("WOPR_BASE_URL", "http://example.local:8080")
    assert _wopr_base_url() == "http://example.local:8080"


def test_strips_trailing_slash(monkeypatch):
    monkeypatch.setenv("WOPR_BASE_URL", "http://example.local:8080/")
    assert _wopr_base_url() == "http://example.local:8080"
