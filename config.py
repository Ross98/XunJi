import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


PROJECT_ENV_PATH = Path(__file__).with_name(".env")
DEFAULT_CACHE_PATH = "xunji_cache.sqlite"


def _cache_path_setting() -> str:
    value = os.environ.get("XUNJI_CACHE_PATH")
    return value if value and value.strip() else DEFAULT_CACHE_PATH


def load_cache_path() -> str:
    """Standalone cache users share the app's path without requiring API keys."""
    load_dotenv(PROJECT_ENV_PATH, override=False)
    return _cache_path_setting()


class ConfigurationError(RuntimeError):
    """Raised when an active XunJi integration is not configured."""


@dataclass
class XunjiConfig:
    # Active integrations
    training_api_key: str
    body_api_key: str

    # Retired diet module. Kept for the legacy code, never required at runtime.
    diet_api_key: str = ""
    diet_search_api_key: str = ""

    # URLs with defaults
    training_base_url: str = "https://trains.xunjiapp.cn"
    diet_base_url: str = "https://eatings.xunjiapp.cn"
    diet_search_base_url: str = "https://api.xunjiapp.cn"
    body_base_url: str = "https://api.xunjiapp.cn"

    # App
    cache_path: str = DEFAULT_CACHE_PATH

    @classmethod
    def from_env(cls) -> "XunjiConfig":
        load_dotenv(PROJECT_ENV_PATH, override=False)
        required = ("XUNJI_API_KEY", "XUNJI_BODY_API_KEY")
        missing = [key for key in required if not os.environ.get(key)]
        if missing:
            raise ConfigurationError(
                "缺少环境变量: "
                f"{', '.join(missing)}。请参考 .env.example 配置训记 Open API。"
            )
        return cls(
            training_api_key=os.environ["XUNJI_API_KEY"],
            body_api_key=os.environ["XUNJI_BODY_API_KEY"],
            diet_api_key=os.environ.get("XUNJI_FOOD_API_KEY", ""),
            diet_search_api_key=os.environ.get("XUNJI_FOOD_SEARCH_API_KEY", ""),
            cache_path=_cache_path_setting(),
        )
