"""The emails to participants end with the invitation to the organisation's news channel
(NEWS_CHANNEL_URL); nothing is added when no channel is configured."""

from api import mailer


def test_no_channel_no_invitation(monkeypatch):
    monkeypatch.setattr(mailer, "NEWS_CHANNEL_URL", "")
    assert mailer.news_channel_text() == ""
    _, body = mailer.build_feedback_resolved({"question": "Come pago la TARI?"}, name="Mario")
    assert "iscriviti" not in body
    assert body.endswith("Grazie per la collaborazione.\n")


def test_channel_invitation_names_the_municipality(monkeypatch):
    monkeypatch.setattr(mailer, "NEWS_CHANNEL_URL", "https://t.me/example")
    monkeypatch.setattr(mailer, "NEWS_CHANNEL_NAME", "canale Telegram")
    monkeypatch.setattr(mailer, "SITE_NAME", "Comune di Esempio")
    text = mailer.news_channel_text()
    assert "Resta aggiornato sulle notizie del Comune di Esempio: iscriviti al canale Telegram" in text
    assert "https://t.me/example" in text
    _, body = mailer.build_feedback_resolved({"question": "Come pago la TARI?"}, name="Mario")
    assert "https://t.me/example" in body
    assert body.endswith("Grazie per la collaborazione.\n")


def test_channel_invitation_other_organisation(monkeypatch):
    monkeypatch.setattr(mailer, "NEWS_CHANNEL_URL", "https://example.org/newsletter")
    monkeypatch.setattr(mailer, "NEWS_CHANNEL_NAME", "newsletter")
    monkeypatch.setattr(mailer, "SITE_NAME", "Biblioteca civica")
    assert "notizie di Biblioteca civica: iscriviti al newsletter" in mailer.news_channel_text()
