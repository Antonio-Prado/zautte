"""clean_text (crawler/crawler.py): the text of a page as it is indexed."""

from crawler.crawler import clean_text


def _page(body: str) -> str:
    return f"<html><head><title>T</title></head><body><main>{body}</main></body></html>"


def test_bold_word_at_the_start_of_a_sentence_is_kept():
    # <strong>Non</strong> used to land on a line of its own and be dropped as noise:
    # the sentence changed meaning ("si possono autenticare procure")
    html = _page("<p><strong>Non</strong>&nbsp;si possono autenticare procure di nessun tipo.</p>"
                 "<p>Per le dichiarazioni di impegno occorre rivolgersi al <a href='/n'>notaio</a>.</p>")
    text = clean_text(html)
    assert "Non si possono autenticare procure di nessun tipo." in text
    assert "rivolgersi al notaio." in text


def test_inline_tags_do_not_split_lines_but_blocks_do():
    html = _page("<p>Marca da bollo da <b>16</b> euro e diritti di <em>0,52</em> euro.</p>"
                 "<ul><li><a href='/a'>Anagrafe</a></li><li><a href='/b'>Stato civile</a></li></ul>")
    lines = clean_text(html).splitlines()
    assert "Marca da bollo da 16 euro e diritti di 0,52 euro." in lines
    assert "Anagrafe" in lines and "Stato civile" in lines


def test_noise_and_short_lines_are_still_dropped():
    html = _page("<p>Condividi</p><p>ok</p><p>Orari di apertura: 9-13.</p>")
    text = clean_text(html)
    assert text == "Orari di apertura: 9-13."


def test_stylesheet_link_with_a_main_id_is_not_the_main_element():
    # WordPress themes and plugins give their stylesheets ids like "main-styles-css": the first
    # element matching id=content|main|body was the <link>, the text came out empty and every
    # page of the tourism site was dropped as too short
    html = ("<html><head><title>T</title>"
            "<link rel='stylesheet' id='acwp-main-css-css' href='/a.css'></head><body>"
            "<link rel='stylesheet' id='main-styles-non-critical-css' href='/b.css'>"
            "<div id='ajax-content-wrap'><h1>Teatro Concordia – Stagione teatrale 2026/2027</h1>"
            "<p>Inizio: giovedì 19 novembre 2026. Fine: giovedì 29 aprile 2027.</p></div></body></html>")
    text = clean_text(html)
    assert "Teatro Concordia – Stagione teatrale 2026/2027" in text
    assert "Inizio: giovedì 19 novembre 2026. Fine: giovedì 29 aprile 2027." in text


def test_an_empty_element_with_a_content_like_class_is_skipped():
    # The first class matching content|main|article was an empty menu icon: the Bollettino
    # articles came out empty
    html = ("<html><head><title>T</title></head><body>"
            "<div class='tdb-main-sub-icon-fake'></div>"
            "<article><p>Fino al 31 luglio si presentano le comunicazioni per gli eventi.</p></article>"
            "</body></html>")
    assert clean_text(html) == "Fino al 31 luglio si presentano le comunicazioni per gli eventi."
