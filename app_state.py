from pathlib import Path

from jinja2 import Environment, FileSystemLoader

from api_client import XunjiAPIClient
from cache import Cache
from config import XunjiConfig
from data_service import DataService

BASE_DIR = Path(__file__).parent
TEMPLATES_DIR = BASE_DIR / "templates"
STATIC_DIR = BASE_DIR / "static"

jinja_env = Environment(
    loader=FileSystemLoader(str(TEMPLATES_DIR)),
    auto_reload=True,
)

config = XunjiConfig.from_env()

_cache = Cache(config.cache_path)
_api_client = XunjiAPIClient(config)
_data_service = DataService(_cache, _api_client)


def get_jinja_env():
    return jinja_env


def get_config():
    return config


def get_data_service():
    return _data_service


def get_api_client():
    return _api_client

def get_cache():
    return _cache
