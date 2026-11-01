"""scripts/news_sync.py: the public web preview of a Telegram channel as a news source."""

from datetime import datetime, timedelta, timezone

from scripts import news_sync

BUM = "https://bum.example.it/2026/10/31/allerta-vento/"
HOSTS = {"www.example.it", "bum.example.it"}


def _message(post_id: str, body_html: str | None, when: str) -> str:
    text = f'<div class="tgme_widget_message_text js-message_text" dir="auto">{body_html}</div>' if body_html else ""
    return (f'<div class="tgme_widget_message_wrap"><div class="tgme_widget_message" data-post="{post_id}">'
            f'<div class="tgme_widget_message_author"><a class="tgme_widget_message_owner_name" href="https://t.me/esempio">'
            f'<span dir="auto">Comune di Esempio</span></a></div>{text}'
            f'<div class="tgme_widget_message_footer"><span class="tgme_widget_message_meta">edited&nbsp;'
            f'<a class="tgme_widget_message_date" href="https://t.me/{post_id}"><time datetime="{when}" class="time">'
            f'13:24</time></a></span></div></div></div>')


ANNOUNCEMENT = ('Allerta per vento e mareggiate per domenica 1 novembre - I fenomeni interessano tutta la costa '
                f'Leggi la notizia <i class="emoji"><b>👇</b></i> <a href="{BUM}" target="_blank">{BUM}</a>')
ONLY_HERE = ('<i class="emoji" style="background-image:url(\'//telegram.org/img/emoji/40/F09F938C.png\')"><b>📌</b></i> '
             "L&#39;allerta gialla per vento e mareggiate è stata prorogata dalla Protezione civile alla giornata "
             "di lunedì 2 novembre.<br/> <br/>Per la prima parte di lunedì sono attese precipitazioni diffuse e continue "
             'con occasionali rovesci più probabili sul settore <br/>meridionale della regione.<br/><br/>'
             'Info: <a href="https://www.protezionecivile.marche.it/allerta" target="_blank">protezionecivile.marche.it</a>')
PAGE = ('<html><body><div class="tgme_channel_info_header_title"><span dir="auto">Comune di Esempio</span></div>'
        + _message("esempio/3441", ANNOUNCEMENT, "2026-10-31T12:35:43+00:00")
        + _message("esempio/3442", ONLY_HERE, "2026-11-01T23:24:19+00:00")
        + _message("esempio/3443", None, "2026-11-01T23:30:00+00:00")       # a photo without caption
        + _message("esempio/3444", "Buona domenica!", "2026-11-02T07:00:00+00:00")
        + "</body></html>")


ROME_MORNING = datetime(2026, 11, 2, 8, 0, tzinfo=timezone(timedelta(hours=1)))


def test_messages_are_parsed_with_permalink_local_date_text_and_links(monkeypatch):
    monkeypatch.setattr(news_sync, "now_local", lambda: ROME_MORNING)
    channel, posts = news_sync.parse_telegram_preview(PAGE)
    assert channel == "Comune di Esempio"
    assert [p["url"] for p in posts] == ["https://t.me/esempio/3441", "https://t.me/esempio/3442", "https://t.me/esempio/3444"]
    alert = posts[1]
    assert alert["date"] == "2026-11-02"            # 23:24 UTC is already the next day in Rome
    assert alert["text"].startswith("📌 L'allerta gialla")
    assert "settore\nmeridionale" in alert["text"]  # the <br> inside a sentence becomes a line break, nothing is lost
    assert alert["links"] == ["https://www.protezionecivile.marche.it/allerta"]
    assert posts[0]["links"] == [BUM]


def test_a_message_that_links_a_site_page_is_an_announcement_the_message_posted_only_there_is_not():
    _, posts = news_sync.parse_telegram_preview(PAGE)
    assert news_sync.telegram_announces_site_page(posts[0], HOSTS)
    assert not news_sync.telegram_announces_site_page(posts[1], HOSTS)   # external link only


def test_document_of_a_message_one_paragraph_with_date_and_channel_short_ones_dropped(monkeypatch):
    monkeypatch.setitem(news_sync.NEWS_SOURCES, "section", "bollettino")
    monkeypatch.setattr(news_sync, "now_local", lambda: ROME_MORNING)
    _, posts = news_sync.parse_telegram_preview(PAGE)
    doc = news_sync.telegram_doc(posts[1], "Comune di Esempio")
    assert doc["url"] == "https://t.me/esempio/3442"
    assert len(doc["chunks"]) == 1
    chunk = doc["chunks"][0]
    title, body = chunk["text"].split("\n\n", 1)           # the chunker puts the title before the text
    assert title == chunk["metadata"]["title"] == ("L'allerta gialla per vento e mareggiate è stata prorogata "
                                                   "dalla Protezione civile alla giornata di - Comune di Esempio")
    assert body.startswith("Messaggio del 2 novembre 2026 sul canale Telegram «Comune di Esempio». 📌 L'allerta gialla")
    assert "\n" not in body                                 # one paragraph, so one chunk
    meta = chunk["metadata"]
    assert meta["source"] == "https://t.me/esempio/3442" and meta["date"] == "2026-11-02"
    assert meta["origin"] == "news" and meta["category"] == "notizia" and meta["channel"] == "telegram"
    assert news_sync.telegram_doc(posts[2], "Comune di Esempio") is None   # «Buona domenica!»


def test_title_skips_a_bare_label_and_a_leading_emoji():
    assert news_sync._telegram_title("AVVISO\nSi ricorda agli automobilisti che domani vige il divieto di sosta") == \
        "AVVISO Si ricorda agli automobilisti che domani vige il divieto di sosta"
    assert news_sync._telegram_title("❗️ Uffici chiusi lunedì 13 ottobre per la festa del Patrono.") == \
        "Uffici chiusi lunedì 13 ottobre per la festa del Patrono"
    long = "Parola " * 30
    assert len(news_sync._telegram_title(long)) <= 100 and not news_sync._telegram_title(long).endswith(" ")


def test_only_a_t_me_s_address_is_accepted(monkeypatch):
    monkeypatch.setitem(news_sync.NEWS_SOURCES, "telegram", "https://t.me/s/esempio/")
    assert news_sync._telegram_preview_url() == "https://t.me/s/esempio"
    assert "t.me" in news_sync._fetch_domains()
    monkeypatch.setitem(news_sync.NEWS_SOURCES, "telegram", "https://t.me/esempio")   # the channel page, not the preview
    assert news_sync._telegram_preview_url() == ""
    monkeypatch.delitem(news_sync.NEWS_SOURCES, "telegram")
    assert news_sync._telegram_preview_url() == ""
