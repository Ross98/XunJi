import sys
import os
from dataclasses import dataclass


@dataclass
class XunjiConfig:
    # Required (no defaults)
    training_api_key: str
    diet_api_key: str
    diet_search_api_key: str
    body_api_key: str

    # URLs with defaults
    training_base_url: str = "https://trains.xunjiapp.cn"
    diet_base_url: str = "https://eatings.xunjiapp.cn"
    diet_search_base_url: str = "https://api.xunjiapp.cn"
    body_base_url: str = "https://api.xunjiapp.cn"

    # App
    cache_path: str = "xunji_cache.sqlite"

    @classmethod
    def from_env(cls) -> "XunjiConfig":
        missing = []
        for key in ["XUNJI_API_KEY", "XUNJI_FOOD_API_KEY", "XUNJI_FOOD_SEARCH_API_KEY", "XUNJI_BODY_API_KEY"]:
            if key not in os.environ:
                missing.append(key)
        if missing:
            print("=" * 60, file=sys.stderr)
            print("  XunJi Analysis 配置错误", file=sys.stderr)
            print("=" * 60, file=sys.stderr)
            print(f"  缺少环境变量: {', '.join(missing)}", file=sys.stderr)
            print(file=sys.stderr)
            print("  请创建 .env 文件，参考 .env.example 填入你的 API Key。", file=sys.stderr)
            print("  从训记 App → 设置 → Open API 获取。", file=sys.stderr)
            print("=" * 60, file=sys.stderr)
            sys.exit(1)
        return cls(
            training_api_key=os.environ["XUNJI_API_KEY"],
            diet_api_key=os.environ["XUNJI_FOOD_API_KEY"],
            diet_search_api_key=os.environ["XUNJI_FOOD_SEARCH_API_KEY"],
            body_api_key=os.environ["XUNJI_BODY_API_KEY"],
        )

