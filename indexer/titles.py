"""
Titoli leggibili per le fonti PDF.

Il titolo salvato di un PDF è il /Title interno del file oppure, se manca, il nome
del file in cache (percorso dell'URL + hash: «Engine_RAServeFile.php_f__Allegato_B.pdf_660…»).
Molti /Title non dicono nulla («COMUNE DI SAN BENEDETTO DEL TRONTO» su 182 file,
«Layout 1», «Microsoft Word - …», «INDICE»). Il titolo da mostrare (fonti del widget,
contesto del modello) si sceglie al volo: il /Title se è informativo, altrimenti il
nome del file preso dall'URL, ripulito. L'indice salvato non cambia.
"""

import re
from urllib.parse import unquote, urlparse

from config.settings import SITE_NAME

# Nome del file in cache del crawler: <percorso>_<md5 dell'URL, 12 cifre hex>
_CACHE_STEM = re.compile(r"_[0-9a-f]{12}$")
_APP_PREFIX = re.compile(
    r"^(?:microsoft\s+(?:office\s+)?(?:word|excel|powerpoint)|libreoffice\s+\w+|openoffice\.org\s+\w+)\s*-\s*",
    re.IGNORECASE,
)
_EXT = re.compile(r"\.(?:pdf|docx?|odt|ods|odp|xlsx?|pptx?|rtf|txt|p7m)$", re.IGNORECASE)
_COPY_MARK = re.compile(r"\s*[\[(]\d{1,2}[\])]$")
_JUNK = {
    "layout 1", "prot", "model", "modello", "indice", "capo", "untitled", "senza titolo",
    "documento", "document", "frontespizio", "pdf", "stampa", "scansione",
    "documento in windows internet explorer",
}
_GENERIC = re.compile(
    r"^(?:allegato|all\.?|documento|doc|file|scan|stampa|determina|avviso|modulo|mod\.?)"
    r"\s*[\w.()]{0,4}$",
    re.IGNORECASE,
)
_NUMERIC = re.compile(r"^[\d\s._]+(?:merge|graffetta)?$", re.IGNORECASE)
_SKIP_FOLDERS = {"f", "pdf", "allegati", "uploads", "wp content", "engine", "files", "documenti"}
_MAX_LEN = 90


def _clean_filename(name: str) -> str:
    """«Ata_Rifiuti_bilancio_2022.pdf» → «Ata Rifiuti bilancio 2022»."""
    name = unquote(name).replace("+", " ").strip()
    name = _EXT.sub("", name)
    name = _COPY_MARK.sub("", name)
    name = re.sub(r"^\d{1,2}[ _]+(?=[^\W\d_])", "", name)  # numero d'ordine: «9_Piano…»
    name = name.replace("_", " ")
    name = re.sub(r"(?<!\d)-|-(?!\d)", " ", name)  # 2023-2025 resta con il trattino
    name = re.sub(r"\s+", " ", name).strip(" .-")
    return name[:1].upper() + name[1:]


def _informative(title: str) -> bool:
    if sum(c.isalpha() for c in title) < 5:
        return False
    low = title.casefold().strip(" .:")
    if low in _JUNK or _GENERIC.match(low) or _NUMERIC.match(low):
        return False
    return not (SITE_NAME and low == SITE_NAME.casefold())


def title_from_url(url: str) -> str:
    """Nome del file nell'URL, ripulito; se è generico («Determina.pdf») si aggiunge
    la cartella più vicina che dice qualcosa: «Determina (Bandi ed Esiti)»."""
    parts = [p for p in urlparse(url).path.split("/") if p]
    if not parts:
        return ""
    name = _clean_filename(parts[-1])
    if _informative(name):
        return name
    for part in reversed(parts[:-1]):
        folder = _clean_filename(part)
        if folder.casefold() in _SKIP_FOLDERS or folder.lower().endswith(".php"):
            continue
        if _informative(folder):
            return f"{name} ({folder})" if name else folder
    return name


def display_title(title: str, url: str, repeated: bool = False) -> str:
    """Titolo da mostrare per un PDF.

    `repeated`: lo stesso /Title compare su molti PDF diversi (intestazioni come
    «Originale di Deliberazione della Giunta Comunale»): allora il nome del file,
    se informativo, distingue meglio un documento dall'altro.
    """
    t = (title or "").strip()
    if _CACHE_STEM.search(t):
        t = ""
    t = _APP_PREFIX.sub("", t)
    if t and (_EXT.search(t) or "_" in t or (" " not in t and "+" in t)):
        t = _clean_filename(t)
    t = re.sub(r"\s+", " ", t)
    from_url = title_from_url(url)
    if t and _informative(t) and not (repeated and _informative(from_url)):
        return _shorten(t)
    if _informative(from_url):
        return _shorten(from_url)
    return _shorten(t or from_url) or (title or "")


def _shorten(title: str) -> str:
    """Taglia a parola intera i titoli più lunghi di _MAX_LEN caratteri."""
    if len(title) <= _MAX_LEN:
        return title
    return title[:_MAX_LEN].rsplit(" ", 1)[0].rstrip(" ,.;:-") + "…"
