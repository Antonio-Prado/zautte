"""
Titoli leggibili per le fonti PDF.

Il titolo salvato di un PDF è il /Title interno del file oppure, se manca, il nome
del file in cache (percorso dell'URL + hash: «Engine_RAServeFile.php_f__Allegato_B.pdf_660…»).
Molti /Title non dicono nulla («COMUNE DI SAN BENEDETTO DEL TRONTO» su 182 file,
«Layout 1», «Microsoft Word - …», «INDICE»). Il titolo da mostrare (fonti del widget,
contesto del modello) si sceglie al volo: il /Title se è informativo, altrimenti il
nome del file preso dall'URL, ripulito; se anche quello è generico, il titolo ricavato
dall'inizio del documento (riga «Oggetto:» o prima riga dopo l'intestazione).
L'indice salvato non cambia.
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


# --- Titolo dal testo del PDF --------------------------------------------------------
# Quando anche il nome del file è generico («Allegato B 2022», «DD 665-25», «Determina
# (Bandi ed Esiti)») il titolo si cerca nell'inizio del documento (primo chunk): la riga
# «Oggetto:» di determine e delibere, altrimenti la prima riga che non è intestazione.

# Parole che da sole non identificano un documento
_WEAK_WORDS = {
    "allegato", "determina", "determinazione", "modulo", "avviso", "documento", "delibera",
    "deliberazione", "decreto", "modello", "domanda", "schema", "scheda", "file", "prot",
    "protocollo", "originale", "copia", "bozza", "verbale", "elenco", "tabella", "annuale",
}
_OGGETTO = re.compile(r"^\s*oggetto\s*[:.]\s*(.*)$", re.IGNORECASE)
_OGGETTO_STOP = re.compile(
    r"^\s*(?:l[’']anno|file con impronta|firmato digitalmente|n\.\s*\d|data\b|classifica\b|"
    r"il dirigente|il responsabile|il sindaco|la giunta|il consiglio|il commissario|"
    r"premess[oa]|vist[oaie]\b|considerato|\[pagina)",
    re.IGNORECASE,
)
_BOILERPLATE = re.compile(
    r"(?:\bcomune di\b|\bcitt[aà][’']? di\b|\bprovincia di\b|\bregione\b|p\.\s*iva|cod\.?\s*fisc|"
    r"\bc\.\s*f\.|\btel\.?\b|\bfax\b|\bpec\b|@|www\.|https?:|^\s*(?:viale|via|piazza|corso)\b|\b63074\b|"
    r"^\s*(?:settore|servizio|ufficio|area|dipartimento)\b|c\.d\.g\b|\bmod\.\s*\d|^\s*classifica\b|"
    r"^\s*prot|^\s*data\b|^\s*n\.\s*\d|^\s*(?:originale|copia) di\b|^\s*(?:spett|al |alla |all[’'])|"
    r"il sottoscritto|^\s*indice\s*$|^\s*pag(?:ina|\.)?\s*\d|_{3,}|\.{4,})",
    re.IGNORECASE,
)
_SPACED = re.compile(r"^(?:\S ){4,}")  # intestazioni con le lettere spaziate: «S E T T O R E»
_CONNECTIVE_END = re.compile(r"\b(?:di|del|della|delle|dei|degli|e|ed|per|a|al|alla|in|con)\s*$", re.IGNORECASE)
_KEEP_UPPER_SMALL = re.compile(r"^\(?[A-Z]{2,4}\)?[:,.]?$")
_TEXT_MAX_LINES = 30


def is_weak(title: str) -> bool:
    """Vero se il titolo non contiene parole che identificano il documento:
    «Allegato B 2022», «DD 665-25», «Determina (Bandi ed Esiti)»."""
    t = re.sub(r"\([^)]*\)", " ", title or "").lower()
    words = [w for w in re.findall(r"[^\W\d_]+", t) if len(w) >= 4 and w not in _WEAK_WORDS]
    return not words


def _tame_caps(s: str) -> str:
    """Testo quasi tutto maiuscolo → minuscolo con l'iniziale maiuscola; restano maiuscole
    le sigle brevi tra parentesi o seguite da «:» (PIAO, CIG) e i codici con cifre o punti."""
    letters = [c for c in s if c.isalpha()]
    if not letters or sum(c.isupper() for c in letters) / len(letters) < 0.7:
        return s
    out = []
    for w in s.split():
        keep = (any(c.isdigit() for c in w) or "." in w.strip(".,;:")
                or (_KEEP_UPPER_SMALL.match(w) and (w.startswith("(") or w.endswith(":"))))
        out.append(w if keep else w.lower())
    t = " ".join(out)
    if SITE_NAME:  # «san benedetto del tronto» → «San Benedetto del Tronto»
        place = re.sub(r"^(?:comune|citt[aà]) di\s+", "", SITE_NAME, flags=re.IGNORECASE)
        for name in {SITE_NAME, place}:
            t = re.sub(re.escape(name), name, t, flags=re.IGNORECASE)
    return t[:1].upper() + t[1:]


def _tidy(s: str) -> str:
    s = re.sub(r"\s+", " ", s).strip(" .;:-–")
    return _tame_caps(s)


def title_from_text(text: str, stored_title: str = "") -> str:
    """Titolo ricavato dall'inizio del PDF (testo del primo chunk), o "" se non si trova."""
    if stored_title and text.startswith(stored_title + "\n\n"):
        text = text[len(stored_title) + 2:]  # il chunker antepone il titolo salvato
    lines = [ln.strip() for ln in text.split("\n")[:_TEXT_MAX_LINES]]
    lines = [ln for ln in lines if ln]
    page = re.compile(r"^\[pagina \d+\]$", re.IGNORECASE)

    # 1. «Oggetto: …» (determine, delibere, decreti), anche su più righe
    for i, ln in enumerate(lines):
        m = _OGGETTO.match(ln)
        if not m:
            continue
        parts = [m.group(1)] if m.group(1) else []
        for nxt in lines[i + 1:i + 8]:
            if _OGGETTO_STOP.match(nxt) or sum(len(p) for p in parts) > 220:
                break
            parts.append(nxt)
        subject = _tidy(" ".join(parts))
        if _informative(subject):
            return subject
        break

    # 2. Prima riga che non è intestazione, unita alle righe brevi che la continuano
    #    («Piano Dettagliato» / «degli» / «Obiettivi» / «2020»)
    prev = ""
    for i, ln in enumerate(lines):
        # coda di un'intestazione andata a capo: «… CONTROLLO DI» / «GESTIONE»
        tail = _BOILERPLATE.search(prev) and _CONNECTIVE_END.search(prev)
        prev = ln
        if (tail or page.match(ln) or _BOILERPLATE.search(ln) or _SPACED.match(ln) or ln.startswith("(")
                or sum(c.isalpha() for c in ln) < 4 or (is_weak(ln) and len(ln) < 25)):
            continue
        parts = [ln]
        for nxt in lines[i + 1:i + 6]:
            if (len(" ".join(parts)) >= 60 or page.match(nxt) or _BOILERPLATE.search(nxt)
                    or nxt.startswith("(") or _SPACED.match(nxt) or len(nxt) > 70):
                break
            parts.append(nxt)
        candidate = _tidy(" ".join(parts))
        return candidate if _informative(candidate) and not is_weak(candidate) else ""
    return ""


def with_text_title(base: str, text_title: str) -> str:
    """«Piano Dettagliato degli Obiettivi 2020 (Allegato B)»: il titolo dal testo (al più
    _MAX_LEN caratteri), con il nome del file tra parentesi quando è breve: aiuta a
    riconoscere l'atto sul sito («… (DD 665-25)»)."""
    if not text_title:
        return base
    head = _shorten(text_title)
    ref = re.sub(r"\s*\([^)]*\)$", "", base).strip()
    if not ref or len(ref) > 35 or ref.casefold() in text_title.casefold():
        return head
    return f"{head} ({ref})"
