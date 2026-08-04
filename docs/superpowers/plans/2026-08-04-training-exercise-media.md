# Training Exercise Media Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 `Ross98/exercises-dataset` 的全量本地动作资源接入 XunJi 训练详情，为可匹配动作显示本地 180×180 GIF。

**Architecture:** 将上游 `data/`、`images/`、`videos/` 和许可证文件作为静态资源放入 `static/exercises/`。新增 `exercise_media.py` 负责一次加载并缓存 JSON、规范化动作名、执行明确别名匹配和生成本地资源 URL；训练路由在模板渲染前为动作附加可选媒体字段，Jinja 模板负责展示与 GIF/JPG 降级。

**Tech Stack:** Python 3.11+, FastAPI, Jinja2, FastAPI `StaticFiles`, pytest, GitHub 上游仓库资源。

## Global Constraints

- 本地保存完整 1,324 条动作数据、1,324 张 JPG 和 1,324 个 GIF。
- 媒体保持上游 180×180 分辨率。
- 保留 `© Gym visual — https://gymvisual.com/` 署名、上游 `LICENSE` 和 `NOTICE.md`。
- 不复制上游 `index.html`、`setup.html` 等演示工具。
- 不运行时从 GitHub 下载资源；浏览器请求只能指向 `/static/exercises/...`。
- 只做精确名称和明确别名匹配，不做模糊匹配。
- 未匹配或资源异常时保持现有文字详情，不渲染破图。
- 不修改训练记录数据结构、训记 API 协议、分析逻辑或动作追踪接口。
- 每个任务完成后运行该任务指定测试，并只提交该任务相关文件。

---

## 文件结构

| 文件 | 职责 |
| --- | --- |
| `static/exercises/data/exercises.json` | 上游 1,324 条动作数据 |
| `static/exercises/data/exercises.schema.json` | 上游 JSON Schema |
| `static/exercises/images/` | 本地 180×180 JPG 缩略图 |
| `static/exercises/videos/` | 本地 180×180 GIF |
| `static/exercises/LICENSE` | 上游 MIT 许可证及媒体例外说明 |
| `static/exercises/NOTICE.md` | Gym visual 媒体署名和使用说明 |
| `docs/exercise-media.md` | XunJi 中的来源、资源布局和更新说明 |
| `exercise_media.py` | 数据加载、规范化、匹配和训练动作标注 |
| `tests/test_exercise_media.py` | 媒体加载、完整性、匹配和不可变性测试 |
| `movements_cn.py` | 保留动作原始名称，继续输出现有中文展示名称 |
| `routes/training.py` | 在两条训练详情渲染路径接入媒体标注 |
| `templates/training_detail.html` | 显示 GIF、署名和 JPG 回退 |
| `static/css/app.css` | 动作媒体布局与署名样式 |

## Task 1: Vendor the Local Dataset

**Files:**
- Create: `static/exercises/data/exercises.json`
- Create: `static/exercises/data/exercises.schema.json`
- Create: `static/exercises/images/` with 1,324 JPG files
- Create: `static/exercises/videos/` with 1,324 GIF files
- Create: `static/exercises/LICENSE`
- Create: `static/exercises/NOTICE.md`
- Create: `docs/exercise-media.md`

**Interfaces:**
- Produces local data root `static/exercises/` consumed by `exercise_media.py`.
- Produces a pinned source note consumed by future maintainers; no runtime API.

- [ ] **Step 1: Download the pinned upstream snapshot into a temporary directory**

Use the upstream commit already inspected for this design, `7455efae41b330c265e7cd4b78dfa848e7ce5ebd`:

```bash
tmp_dir="$(mktemp -d)"
trap 'rm -rf "$tmp_dir"' EXIT
git clone --depth 1 https://github.com/Ross98/exercises-dataset.git "$tmp_dir/exercises-dataset"
git -C "$tmp_dir/exercises-dataset" fetch --depth 1 origin 7455efae41b330c265e7cd4b78dfa848e7ce5ebd
git -C "$tmp_dir/exercises-dataset" checkout --detach 7455efae41b330c265e7cd4b78dfa848e7ce5ebd
```

Expected: `git rev-parse HEAD` prints `7455efae41b330c265e7cd4b78dfa848e7ce5ebd`.

- [ ] **Step 2: Copy only approved source directories and license files**

```bash
mkdir -p static/exercises/data
cp "$tmp_dir/exercises-dataset/data/exercises.json" static/exercises/data/
cp "$tmp_dir/exercises-dataset/data/exercises.schema.json" static/exercises/data/
cp -R "$tmp_dir/exercises-dataset/images" static/exercises/
cp -R "$tmp_dir/exercises-dataset/videos" static/exercises/
cp "$tmp_dir/exercises-dataset/LICENSE" static/exercises/
cp "$tmp_dir/exercises-dataset/NOTICE.md" static/exercises/
```

Do not copy `index.html`, `setup.html`, upstream README, or the upstream `.git` directory.

- [ ] **Step 3: Add the local source documentation**

Create `docs/exercise-media.md` with these exact facts:

```markdown
# 训练动作媒体

资源来源：[Ross98/exercises-dataset](https://github.com/Ross98/exercises-dataset)

当前固定快照：`7455efae41b330c265e7cd4b78dfa848e7ce5ebd`

本地资源：

- `static/exercises/data/`：动作 JSON 和 Schema
- `static/exercises/images/`：1,324 张 180×180 JPG
- `static/exercises/videos/`：1,324 个 180×180 GIF
- `static/exercises/LICENSE`、`static/exercises/NOTICE.md`：许可证和媒体说明

媒体版权归 Gym visual 所有。使用媒体时保留：
`© Gym visual — https://gymvisual.com/`

本项目当前为个人本地使用。公开部署或分发前，重新核对 Gym visual 条款并按要求取得许可。
```

- [ ] **Step 4: Validate the vendored asset counts and references**

```bash
python3 - <<'PY'
import json
from pathlib import Path

root = Path("static/exercises")
records = json.loads((root / "data/exercises.json").read_text(encoding="utf-8"))
assert len(records) == 1324
assert len(list((root / "images").glob("*.jpg"))) == 1324
assert len(list((root / "videos").glob("*.gif"))) == 1324
for record in records:
    assert (root / record["image"]).is_file(), record["image"]
    assert (root / record["gif_url"]).is_file(), record["gif_url"]
assert (root / "LICENSE").is_file()
assert (root / "NOTICE.md").is_file()
print("validated 1324 exercise records and media pairs")
PY
```

Expected: `validated 1324 exercise records and media pairs`.

- [ ] **Step 5: Commit only vendored resources and source documentation**

```bash
git add static/exercises docs/exercise-media.md
git commit -m "chore(assets): vendor exercise media"
```

## Task 2: Define Failing Catalog and Matching Tests

**Files:**
- Create: `tests/test_exercise_media.py`
- Test: `tests/test_exercise_media.py`

**Interfaces:**
- Consumes: Task 1 local dataset.
- Expects `exercise_media.py` to expose `EXERCISE_ASSET_ROOT`, `find_exercise_media`, `attach_exercise_media`, and `normalize_exercise_name`.

- [ ] **Step 1: Write tests for deterministic normalization and lookup**

Create the test file with:

```python
import copy
import json

from exercise_media import (
    EXERCISE_ASSET_ROOT,
    attach_exercise_media,
    find_exercise_media,
    normalize_exercise_name,
)


def test_normalize_exercise_name_removes_training_suffix_and_camel_case():
    assert normalize_exercise_name("杠铃卧推_胸部训练") == "杠铃卧推"
    assert normalize_exercise_name("BarbellBenchPress") == "barbell bench press"
    assert normalize_exercise_name("barbell bench press") == "barbell bench press"


def test_find_exercise_media_matches_chinese_alias():
    media = find_exercise_media("杠铃卧推_胸部训练")

    assert media == {
        "id": "0025",
        "name": "barbell bench press",
        "gif_url": "/static/exercises/videos/0025-EIeI8Vf.gif",
        "image_url": "/static/exercises/images/0025-EIeI8Vf.jpg",
        "attribution": "© Gym visual — https://gymvisual.com/",
    }


def test_find_exercise_media_prefers_original_english_name():
    media = find_exercise_media(
        "杠铃卧推_胸部训练", source_name="barbell bench press"
    )

    assert media["id"] == "0025"
    assert media["gif_url"].endswith("/videos/0025-EIeI8Vf.gif")


def test_unknown_exercise_has_no_media():
    assert find_exercise_media("不存在的动作_胸部训练") is None


def test_attach_exercise_media_does_not_mutate_input():
    trains = [{"movements": [{"name": "杠铃卧推_胸部训练"}]}]
    original = copy.deepcopy(trains)

    result = attach_exercise_media(trains)

    assert trains == original
    assert result[0]["movements"][0]["media"]["id"] == "0025"


def test_all_dataset_media_files_exist():
    records = json.loads(
        (EXERCISE_ASSET_ROOT / "data/exercises.json").read_text(encoding="utf-8")
    )

    assert len(records) == 1324
    for record in records:
        assert (EXERCISE_ASSET_ROOT / record["image"]).is_file()
        assert (EXERCISE_ASSET_ROOT / record["gif_url"]).is_file()
```

- [ ] **Step 2: Run the focused tests and verify they fail for the intended reason**

Run:

```bash
pytest tests/test_exercise_media.py -q
```

Expected: collection fails with `ModuleNotFoundError: No module named 'exercise_media'` because the implementation has not been added.

- [ ] **Step 3: Commit the behavior specification tests**

```bash
git add tests/test_exercise_media.py
git commit -m "test(training): define exercise media matching"
```

## Task 3: Implement the Local Exercise Catalog

**Files:**
- Create: `exercise_media.py`
- Test: `tests/test_exercise_media.py`

**Interfaces:**
- Consumes: `static/exercises/data/exercises.json`.
- Produces:
  - `EXERCISE_ASSET_ROOT: Path`
  - `normalize_exercise_name(name: str) -> str`
  - `find_exercise_media(name: str, source_name: str | None = None) -> dict[str, str] | None`
  - `attach_exercise_media(trains: list[dict]) -> list[dict]`

- [ ] **Step 1: Implement normalization, index loading, and explicit aliases**

Implement `exercise_media.py` with these rules:

```python
from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path

EXERCISE_ASSET_ROOT = Path(__file__).parent / "static" / "exercises"
_DATA_PATH = EXERCISE_ASSET_ROOT / "data" / "exercises.json"
_ATTRIBUTION = "© Gym visual — https://gymvisual.com/"

_CN_ALIASES = {
    "杠铃卧推": "barbell bench press",
    "哑铃卧推": "dumbbell bench press",
    "杠铃硬拉": "barbell deadlift",
    "杠铃深蹲": "barbell full squat",
    "杠铃全蹲": "barbell full squat",
    "哑铃弯举": "dumbbell biceps curl",
    "引体向上": "pull-up",
    "俯卧撑": "push-up",
}


def normalize_exercise_name(name: str) -> str:
    """Normalize deterministic spelling and XunJi category suffix differences."""
    if not name:
        return ""
    value = name.strip()
    value = re.sub(r"[_\s-]+[\u4e00-\u9fff]{1,12}训练$", "", value)
    value = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", value)
    value = value.lower().replace("–", "-").replace("—", "-")
    value = re.sub(r"[^0-9a-z\u4e00-\u9fff]+", " ", value)
    return re.sub(r"\s+", " ", value).strip()
```

Build the cached index from the JSON records using normalized `record["name"]` keys. Ignore malformed records rather than preventing the page from loading. Convert only safe `images/...` and `videos/...` relative paths into `/static/exercises/...` URLs; reject absolute paths and paths containing `..`.

- [ ] **Step 2: Implement lookup priority and immutable train annotation**

`find_exercise_media` must try `source_name` first, then display `name`. For each candidate, try the normalized dataset name and then the normalized Chinese alias. Return a new dictionary containing exactly `id`, `name`, `gif_url`, `image_url`, and `attribution`.

Use this implementation shape for the remaining functions:

```python
from pathlib import PurePosixPath


@lru_cache(maxsize=1)
def _exercise_index() -> dict[str, dict]:
    try:
        records = json.loads(_DATA_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(records, list):
        return {}
    index = {}
    for record in records:
        if not isinstance(record, dict):
            continue
        raw_name = record.get("name")
        key = normalize_exercise_name(raw_name) if isinstance(raw_name, str) else ""
        if key:
            index.setdefault(key, record)
    return index


def _safe_asset_url(relative_path: str, expected_dir: str) -> str | None:
    path = PurePosixPath(relative_path)
    if (
        len(path.parts) < 2
        or path.parts[0] != expected_dir
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        return None
    return f"/static/exercises/{path.as_posix()}"


def _candidate_keys(value: str | None):
    normalized = normalize_exercise_name(value or "")
    if not normalized:
        return
    yield normalized
    alias_target = _CN_ALIASES.get(normalized)
    if alias_target:
        yield normalize_exercise_name(alias_target)


def find_exercise_media(
    name: str, source_name: str | None = None
) -> dict[str, str] | None:
    index = _exercise_index()
    seen = set()
    for candidate in (source_name, name):
        for key in _candidate_keys(candidate):
            if key in seen:
                continue
            seen.add(key)
            record = index.get(key)
            if not record:
                continue
            gif_url = _safe_asset_url(record.get("gif_url", ""), "videos")
            image_url = _safe_asset_url(record.get("image", ""), "images")
            if not gif_url or not image_url:
                continue
            return {
                "id": str(record.get("id", "")),
                "name": str(record.get("name", "")),
                "gif_url": gif_url,
                "image_url": image_url,
                "attribution": str(record.get("attribution") or _ATTRIBUTION),
            }
    return None


def attach_exercise_media(trains: list[dict]) -> list[dict]:
    result = []
    for train in trains:
        copied_train = dict(train)
        movements = train.get("movements")
        if isinstance(movements, list):
            copied_movements = []
            for movement in movements:
                if not isinstance(movement, dict):
                    copied_movements.append(movement)
                    continue
                copied_movement = dict(movement)
                copied_movement.pop("media", None)
                media = find_exercise_media(
                    copied_movement.get("name", ""),
                    copied_movement.get("_source_name"),
                )
                if media:
                    copied_movement["media"] = media
                copied_movements.append(copied_movement)
            copied_train["movements"] = copied_movements
        result.append(copied_train)
    return result
```

`attach_exercise_media` must copy each train and movement dictionary. Add `media` only when lookup succeeds; remove a stale `media` key when lookup fails. Preserve lists and fields not owned by the media module. Never mutate the input object.

- [ ] **Step 3: Run the focused tests and verify the implementation passes**

Run:

```bash
pytest tests/test_exercise_media.py -q
```

Expected: all focused tests pass.

- [ ] **Step 4: Commit the catalog implementation**

```bash
git add exercise_media.py tests/test_exercise_media.py
git commit -m "feat(training): add local exercise media catalog"
```

## Task 4: Preserve Original Names for Media Lookup

**Files:**
- Modify: `movements_cn.py:112-128` in `apply_movement_cn_to_trains`
- Modify: `tests/test_exercise_media.py`

**Interfaces:**
- Consumes: existing movement dictionaries returned by the API.
- Produces: the existing display `name` plus private `_source_name` for media lookup.

- [ ] **Step 1: Add the regression test**

Append:

```python
def test_apply_movement_cn_preserves_source_name():
    from movements_cn import apply_movement_cn_to_trains

    trains = [{"movements": [{"name": "BarbellBenchPress"}]}]

    result = apply_movement_cn_to_trains(trains)

    movement = result[0]["movements"][0]
    assert movement["_source_name"] == "BarbellBenchPress"
    assert movement["name"] == "杠铃卧推"
```

- [ ] **Step 2: Run the test and verify the new assertion fails**

Run:

```bash
pytest tests/test_exercise_media.py::test_apply_movement_cn_preserves_source_name -q
```

Expected: FAIL with `KeyError: '_source_name'`.

- [ ] **Step 3: Preserve the raw name while keeping display behavior unchanged**

In `apply_movement_cn_to_trains`, replace the movement loop body with this shape. Keep all existing generic-type and Chinese-name behavior unchanged:

```python
raw_name = m.get("name", "")
name = raw_name
if name in GENERIC_TYPES and title:
    name = title
else:
    name = movement_name_cn(name)
updated.append({**m, "_source_name": raw_name, "name": name})
```

- [ ] **Step 4: Run focused movement and media tests**

```bash
pytest tests/test_exercise_media.py tests/test_training.py -q
```

Expected: all selected tests pass.

- [ ] **Step 5: Commit the source-name preservation**

```bash
git add movements_cn.py tests/test_exercise_media.py
git commit -m "feat(training): preserve source movement names"
```

## Task 5: Annotate Both Training Detail Render Paths

**Files:**
- Modify: `routes/training.py:224-246` in `training_detail_fragment`
- Modify: `routes/training.py:275-298` in `training_page`
- Test: `tests/test_exercise_media.py`

**Interfaces:**
- Consumes: `attach_exercise_media(trains)` from Task 3 and `_source_name` from Task 4.
- Produces: `trains` entries with optional `movement["media"]` for `training_detail.html`.

- [ ] **Step 1: Add a route-boundary regression test helper case**

Add a test that confirms an already merged/display-ready movement can be annotated without changing summary fields:

```python
def test_attach_exercise_media_preserves_training_fields():
    trains = [{
        "title": "胸部训练",
        "start": 100,
        "movements": [{"name": "杠铃卧推", "sets": [{"weight": "60", "reps": "5"}]}],
    }]

    result = attach_exercise_media(trains)

    assert result[0]["title"] == "胸部训练"
    assert result[0]["start"] == 100
    assert result[0]["movements"][0]["sets"] == [{"weight": "60", "reps": "5"}]
    assert result[0]["movements"][0]["media"]["id"] == "0025"
```

- [ ] **Step 2: Run the regression test**

```bash
pytest tests/test_exercise_media.py::test_attach_exercise_media_preserves_training_fields -q
```

Expected: PASS from Task 3.

- [ ] **Step 3: Add media annotation after final train shaping in both routes**

Import `attach_exercise_media` in `routes/training.py`. Apply it after final merge in both render paths:

```python
trains = _merge_overlapping_trains(trains)
trains = attach_exercise_media(trains)
```

```python
today_trains = _merge_overlapping_trains(today_trains)
today_trains = attach_exercise_media(today_trains)
```

Do not annotate `all_trains` used only by charts or tracking.

- [ ] **Step 4: Run route and analysis regression tests**

```bash
pytest tests/test_training.py tests/test_analysis.py tests/test_planner.py -q
```

Expected: all selected tests pass.

- [ ] **Step 5: Commit route integration**

```bash
git add routes/training.py tests/test_exercise_media.py
git commit -m "feat(training): attach media to detail records"
```

## Task 6: Render GIF, Attribution, and JPG Fallback

**Files:**
- Modify: `templates/training_detail.html:19-55`
- Modify: `static/css/app.css`

**Interfaces:**
- Consumes: optional `mov.media` dictionary from Task 5.
- Produces: local GIF image, local JPG fallback, alt text, and attribution without changing the existing set display.

- [ ] **Step 1: Add the media block around each matched movement**

Keep the current movement name and set-rendering branches unchanged. Wrap the movement content in a row and add this block adjacent to it:

```jinja2
{% if mov.get("media") %}
<figure class="exercise-media">
    <img
        src="{{ mov.media.gif_url }}"
        data-fallback="{{ mov.media.image_url }}"
        width="180"
        height="180"
        loading="lazy"
        alt="{{ mov.name }}动作示意图"
        onerror="if (!this.dataset.fallbackUsed) { this.dataset.fallbackUsed = '1'; this.src = this.dataset.fallback; } else { this.closest('figure').hidden = true; }"
    >
    <figcaption>{{ mov.media.attribution }}</figcaption>
</figure>
{% endif %}
```

Do not render the block when `mov.media` is absent.

- [ ] **Step 2: Add scoped CSS for desktop and narrow screens**

Add styles in `static/css/app.css` with these values:

```css
.movement-media-row {
    display: flex;
    align-items: flex-start;
    gap: 12px;
}

.exercise-media {
    flex: 0 0 180px;
    width: 180px;
    margin: 0;
}

.exercise-media img {
    display: block;
    width: 180px;
    height: 180px;
    object-fit: contain;
    border-radius: 6px;
    background: #f7f7f7;
}

.exercise-media figcaption {
    margin-top: 4px;
    color: #888;
    font-size: 10px;
    line-height: 1.3;
}

@media (max-width: 640px) {
    .movement-media-row {
        display: block;
    }

    .exercise-media {
        margin-top: 8px;
    }
}
```

- [ ] **Step 3: Render the template with matched and unmatched movements**

Run the app locally:

```bash
uvicorn app:app --host 127.0.0.1 --port 8000
```

Open a training detail containing a mapped movement and verify the browser requests `/static/exercises/videos/0025-EIeI8Vf.gif`, shows the GIF and attribution, and keeps the set data visible. Verify an unmapped movement has no empty image element.

- [ ] **Step 4: Commit the detail presentation**

```bash
git add templates/training_detail.html static/css/app.css
git commit -m "feat(training): show exercise media in details"
```

## Task 7: Full Verification and Handoff

**Files:**
- Verify: `static/exercises/`
- Verify: `exercise_media.py`
- Verify: `movements_cn.py`
- Verify: `routes/training.py`
- Verify: `templates/training_detail.html`
- Verify: `static/css/app.css`
- Verify: `docs/exercise-media.md`

**Interfaces:**
- Consumes: all prior task commits.
- Produces: verified local-only media integration with no unrelated files staged.

- [ ] **Step 1: Run all automated tests**

```bash
pytest -q
```

Expected: exit code 0 and all tests pass.

- [ ] **Step 2: Run syntax and diff checks**

```bash
python3 -m compileall -q exercise_media.py movements_cn.py routes templates
git diff --check HEAD~6..HEAD
git status --short
```

Expected: compile succeeds, diff check prints nothing, and status shows only known user changes plus the task's intended commits.

- [ ] **Step 3: Re-run the complete asset integrity check**

```bash
python3 - <<'PY'
import json
from pathlib import Path

root = Path("static/exercises")
records = json.loads((root / "data/exercises.json").read_text(encoding="utf-8"))
assert len(records) == 1324
assert all((root / r["image"]).is_file() for r in records)
assert all((root / r["gif_url"]).is_file() for r in records)
assert "© Gym visual" in (root / "NOTICE.md").read_text(encoding="utf-8")
print("exercise media integration assets verified")
PY
```

Expected: `exercise media integration assets verified`.

- [ ] **Step 4: Review the final diff for scope and legal metadata**

Confirm no upstream demo pages, `.git` files, remote media URLs, or unrelated user modifications were staged. Confirm `LICENSE`, `NOTICE.md`, and the attribution text remain present.
