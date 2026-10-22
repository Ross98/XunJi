import re

from app_state import STATIC_DIR, get_jinja_env


def _relative_luminance(hex_color):
    channels = [int(hex_color[index : index + 2], 16) / 255 for index in (1, 3, 5)]
    linear = [
        value / 12.92 if value <= 0.04045 else ((value + 0.055) / 1.055) ** 2.4
        for value in channels
    ]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def _contrast_ratio(first, second):
    lighter, darker = sorted(
        (_relative_luminance(first), _relative_luminance(second)), reverse=True
    )
    return (lighter + 0.05) / (darker + 0.05)


def _theme_tokens(block):
    return dict(re.findall(r"(--[\w-]+):\s*(#[0-9a-fA-F]{6})", block))


def test_stylesheet_declares_system_dark_mode_and_readable_theme_tokens():
    css = (STATIC_DIR / "css" / "app.css").read_text()

    assert "color-scheme: light dark" in css
    assert "@media (prefers-color-scheme: dark)" in css
    for token in (
        "--page-bg",
        "--surface",
        "--text-primary",
        "--text-muted",
        "--border-color",
        "--chart-grid",
        "--chart-text",
    ):
        assert token in css


def test_primary_and_small_muted_text_meet_wcag_contrast_in_both_themes():
    css = (STATIC_DIR / "css" / "app.css").read_text()
    root_blocks = re.findall(r":root\s*\{([^}]+)\}", css, re.DOTALL)

    assert len(root_blocks) >= 2
    for theme in map(_theme_tokens, root_blocks[:2]):
        for text_token in ("--text-primary", "--text-muted", "--chart-text"):
            assert _contrast_ratio(theme[text_token], theme["--surface"]) >= 4.5


def test_base_template_tracks_system_theme_and_rethemes_live_charts():
    html = get_jinja_env().get_template("base.html").render()

    assert 'name="color-scheme" content="light dark"' in html
    assert "matchMedia('(prefers-color-scheme: dark)')" in html
    assert "addEventListener('change', applyTheme)" in html
    assert "Chart.instances" in html
    assert "xunjiSystemTheme" in html
    assert "backdropColor = colors.surface" in html
    assert "darkChartPalette" in html
    assert "themeColorMeta" in html
    assert "app.css?v=20261022-readable-text" in html


def test_theme_picker_offers_system_light_and_dark_and_persists_choice():
    html = get_jinja_env().get_template("base.html").render()

    assert 'id="themeModeSelect"' in html
    assert 'value="system"' in html
    assert 'value="light"' in html
    assert 'value="dark"' in html
    assert "xunji_theme_mode" in html
    assert "localStorage.setItem(THEME_STORAGE_KEY, mode)" in html
    assert "setMode: setThemeMode" in html
    assert html.index("xunji_theme_mode") < html.index('/static/css/app.css')


def test_stylesheet_allows_manual_light_and_dark_overrides():
    css = (STATIC_DIR / "css" / "app.css").read_text()

    assert 'html[data-theme="light"]' in css
    assert 'html[data-theme="dark"]' in css


def test_freshness_bar_uses_semantic_status_classes():
    html = get_jinja_env().get_template("_freshness_bar.html").render(
        freshness={
            "ah_status": "fresh",
            "ah_tag": "今天",
            "ah_overall_latest": "2026-08-09",
        }
    )

    assert 'class="freshness-bar freshness-fresh"' in html
    assert "background:#e8f5e9" not in html
