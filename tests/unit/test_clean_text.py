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
