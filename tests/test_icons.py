import json
from pathlib import Path

import app
from app_state import STATIC_DIR, get_jinja_env


EXPECTED_ICONS = {
    "favicon.ico",
    "favicon-16.png",
    "favicon-32.png",
    "apple-touch-icon.png",
    "icon-192.png",
    "icon-512.png",
}


def test_icon_assets_are_published_from_static_directory():
    icon_dir = STATIC_DIR / "icons"

    assert {path.name for path in icon_dir.iterdir()} >= EXPECTED_ICONS
    assert all((icon_dir / name).stat().st_size > 0 for name in EXPECTED_ICONS)


def test_manifest_references_existing_install_icons():
    manifest_path = STATIC_DIR / "site.webmanifest"
    manifest = json.loads(manifest_path.read_text())

    assert manifest["name"] == "训记分析"
    assert manifest["display"] == "standalone"
    for icon in manifest["icons"]:
        assert icon["src"].startswith("/static/icons/")
        assert (STATIC_DIR.parent / icon["src"].lstrip("/")).exists()


def test_base_template_links_favicon_touch_icon_and_manifest():
    html = get_jinja_env().get_template("base.html").render()

    assert '/static/icons/favicon.ico' in html
    assert '/static/icons/apple-touch-icon.png' in html
    assert '/static/site.webmanifest' in html


def test_root_favicon_route_is_registered():
    assert any(route.path == "/favicon.ico" for route in app.app.routes)
