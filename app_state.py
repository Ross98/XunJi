from pathlib import Path

from jinja2 import Environment, FileSystemLoader

from api_client import XunjiAPIClient
from cache import Cache
from config import XunjiConfig
from data_service import DataService
from analysis import iter_movement_sets

BASE_DIR = Path(__file__).parent
TEMPLATES_DIR = BASE_DIR / "templates"
STATIC_DIR = BASE_DIR / "static"

jinja_env = Environment(
    loader=FileSystemLoader(str(TEMPLATES_DIR)),
    auto_reload=True,
)
jinja_env.globals["iter_movement_sets"] = iter_movement_sets

_config = None
_cache = None
_api_client = None
_data_service = None


def _ensure_state() -> None:
    global _config, _cache, _api_client, _data_service
    if _data_service is not None:
        return
    _config = XunjiConfig.from_env()
    _cache = Cache(_config.cache_path)
    _api_client = XunjiAPIClient(_config)
    _data_service = DataService(_cache, _api_client)


def get_jinja_env():
    return jinja_env


def get_config():
    _ensure_state()
    return _config


def get_data_service():
    _ensure_state()
    return _data_service


def get_api_client():
    _ensure_state()
    return _api_client


def get_cache():
    _ensure_state()
    return _cache


async def close_state() -> None:
    global _config, _cache, _api_client, _data_service
    try:
        if _data_service is not None:
            await _data_service.close()
    finally:
        _config = None
        _cache = None
        _api_client = None
        _data_service = None
