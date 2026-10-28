"""Console static asset tests: vendored Chart.js (no CDN), tab navigation,
theme toggle (light/dark) and the WebSocket client wiring.
"""

from __future__ import annotations

from pathlib import Path

STATIC = Path(__file__).resolve().parents[1] / "app" / "static"


def test_no_cdn_dependencies():
    """The console must work offline (RNF: no external CDN)."""
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    js = (STATIC / "app.js").read_text(encoding="utf-8")
    for text in (html, js):
        for banned in ("https://cdn", "http://cdn", "unpkg.com", "jsdelivr", "cdnjs"):
            assert banned not in text, f"external dependency found: {banned}"


def test_chartjs_vendored_with_mit_license():
    vendor = STATIC / "vendor" / "chart.umd.js"
    assert vendor.is_file(), "Chart.js UMD bundle must be vendored"
    head = vendor.read_text(encoding="utf-8", errors="ignore")[:2000].lower()
    assert "mit license" in head, "vendored Chart.js must keep its MIT header"
    assert "chart.js" in head


def test_index_has_five_tabs_and_theme_toggle():
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    for tab in ("Dashboard", "CRM", "Automa", "Assistente", "Dados"):
        assert tab in html, f"missing console tab: {tab}"
    assert 'data-theme' in html or 'applyTheme' in html or 'theme' in html.lower()
    assert "theme-toggle" in html or "themeToggle" in html


def test_styles_define_light_and_dark_themes():
    css = (STATIC / "styles.css").read_text(encoding="utf-8")
    assert '[data-theme="light"]' in css
    assert ':root' in css and '--' in css


def test_websocket_client_wired():
    js = "".join(p.read_text(encoding="utf-8") for p in STATIC.glob("*.js"))
    assert "/ws/events" in js, "console must connect to WS /ws/events"
    assert "after" in js, "WS reconnect must use the after cursor"
    snapshot_ok = "reconcil" in js.lower() or "/v1/events" in js
    assert snapshot_ok, "WS must reconcile with the REST snapshot"


def test_extension_modules_exist():
    for name in ("dashboard", "crm", "automations", "agent", "data", "ws", "theme"):
        path = STATIC / f"{name}.js"
        assert path.is_file(), f"missing console module: {name}.js"
        text = path.read_text(encoding="utf-8")
        assert text.strip(), f"console module is empty: {name}.js"


def test_v03_modules_escape_server_data():
    """XSS regression: the v0.3 modules render server-owned strings through
    HTML escaping (the console interpolates user-controlled payment/CRM data
    such as descriptions, names and e-mails)."""
    for name in ("dashboard", "crm", "automations", "agent", "data"):
        text = (STATIC / f"{name}.js").read_text(encoding="utf-8")
        assert "escapeHtml" in text, f"{name}.js must escape server data (XSS)"
