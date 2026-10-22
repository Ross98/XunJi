"""Atomic writes for private, local dashboard preferences."""
import json
import os
import tempfile
from pathlib import Path


def write_json_atomic(path, data: dict) -> None:
    path = Path(path)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent,
            prefix=".settings-", suffix=".tmp", delete=False,
        ) as target:
            temporary = Path(target.name)
            json.dump(data, target, ensure_ascii=False, indent=2)
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
