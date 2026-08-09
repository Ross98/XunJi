import config
import pytest


def test_from_env_requires_only_active_api_keys(monkeypatch):
    monkeypatch.setattr(config, "load_dotenv", lambda *args, **kwargs: None)
    monkeypatch.setenv("XUNJI_API_KEY", "training-key")
    monkeypatch.setenv("XUNJI_BODY_API_KEY", "body-key")
    monkeypatch.delenv("XUNJI_FOOD_API_KEY", raising=False)
    monkeypatch.delenv("XUNJI_FOOD_SEARCH_API_KEY", raising=False)

    result = config.XunjiConfig.from_env()

    assert result.training_api_key == "training-key"
    assert result.body_api_key == "body-key"
    assert result.diet_api_key == ""
    assert result.diet_search_api_key == ""


def test_from_env_loads_project_dotenv_before_validation(monkeypatch):
    loaded = []

    def fake_load_dotenv(path, override=False):
        loaded.append((path, override))
        monkeypatch.setenv("XUNJI_API_KEY", "from-dotenv")
        monkeypatch.setenv("XUNJI_BODY_API_KEY", "from-dotenv")

    monkeypatch.setattr(config, "load_dotenv", fake_load_dotenv)
    monkeypatch.delenv("XUNJI_API_KEY", raising=False)
    monkeypatch.delenv("XUNJI_BODY_API_KEY", raising=False)

    result = config.XunjiConfig.from_env()

    assert result.training_api_key == "from-dotenv"
    assert loaded == [(config.PROJECT_ENV_PATH, False)]


def test_from_env_reports_only_missing_active_integrations(monkeypatch):
    monkeypatch.setattr(config, "load_dotenv", lambda *args, **kwargs: None)
    monkeypatch.delenv("XUNJI_API_KEY", raising=False)
    monkeypatch.delenv("XUNJI_BODY_API_KEY", raising=False)

    with pytest.raises(config.ConfigurationError) as exc_info:
        config.XunjiConfig.from_env()

    message = str(exc_info.value)
    assert "XUNJI_API_KEY" in message
    assert "XUNJI_BODY_API_KEY" in message
    assert "FOOD" not in message
