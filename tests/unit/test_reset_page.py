import asyncio

from api import auth


def test_password_page_carries_logo_name_links_and_footer(monkeypatch):
    monkeypatch.setattr(auth, "AUTH_ENABLED", True)
    monkeypatch.setattr(auth, "SITE_LOGO_URL", "/widget/zautte-logo.svg")
    monkeypatch.setattr(auth, "SITE_NAME", "Comune di Esempio")
    monkeypatch.setattr(auth, "SITE_INFO_URL", "/widget/come-funziona.html")
    monkeypatch.setattr(auth, "PRIVACY_NOTICE_URL", "https://bot.example.org/widget/informativa.html")
    monkeypatch.setattr(auth, "PILOT_LOGIN_URL", "https://bot.example.org/widget/dashboard.html")
    page = asyncio.run(auth.reset_page()).body.decode()
    for needle in ('src="/widget/zautte-logo.svg"', "Comune di Esempio", 'href="/widget/come-funziona.html"',
                   "Informativa sul trattamento dei dati", "/widget/site-footer.js", 'id="go" hidden'):
        assert needle in page


def test_unsafe_logo_and_links_are_left_out(monkeypatch):
    monkeypatch.setattr(auth, "AUTH_ENABLED", True)
    monkeypatch.setattr(auth, "SITE_LOGO_URL", "javascript:alert(1)")
    monkeypatch.setattr(auth, "SITE_INFO_URL", "data:text/html,x")
    page = asyncio.run(auth.reset_page()).body.decode()
    assert "javascript:" not in page and "data:text" not in page and "brand-logo" not in page
