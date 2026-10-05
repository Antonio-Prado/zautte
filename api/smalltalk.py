"""
Messaggi senza una domanda: saluti, ringraziamenti, complimenti, congedi e insulti.

«grazie», «bravo», «ciao» o un insulto non chiedono nulla al sito. Prima
passavano dalla ricerca e dal modello linguistico (costo, e il testo arrivava al
fornitore esterno) e finivano in gaps.jsonl come se fossero lacune nei
contenuti. Qui vengono riconosciuti con una regola semplice e ricevono una
risposta fissa, senza ricerca né modello.

La regola: il messaggio è breve e fatto SOLO di parole di cortesia o insulti più
qualche parola di contorno («sei», «molto», «mille», «per», «la»...). Basta una
parola di contenuto («grazie, e per la TARI?», «non è chiaro», «la merda del
cane sul marciapiede») perché il messaggio segua il percorso normale.
"""

import re

_MAX_WORDS = 8
_MAX_CHARS = 80

# Parole che da sole fanno scattare la risposta fissa, per tipo e lingua
_TRIGGERS: dict[str, dict[str, set[str]]] = {
    "insult": {
        "it": {
            "stronzo", "stronza", "stronzi", "stronze", "stronzata", "stronzate",
            "idiota", "idioti", "idiote", "cretino", "cretina", "cretini", "cretine",
            "scemo", "scema", "scemi", "sceme", "stupido", "stupida", "stupidi", "stupide",
            "imbecille", "imbecilli", "deficiente", "deficienti", "demente", "dementi",
            "coglione", "cogliona", "coglioni", "cazzone", "cazzo", "minchia",
            "vaffanculo", "fanculo", "affanculo", "merda", "schifo", "inutile", "inutili",
            "incapace", "incapaci", "ignorante", "ignoranti", "somaro", "somara", "babbeo",
            "rincoglionito", "rincoglionita", "pirla", "sfigato", "sfigata",
            "bastardo", "bastarda", "puttana",
        },
        "en": {
            "stupid", "idiot", "idiots", "dumb", "useless", "moron", "fuck", "fucking",
            "shit", "crap", "asshole", "suck", "sucks", "bastard",
        },
    },
    "thanks": {
        "it": {
            "grazie", "grazi", "ringrazio", "ringraziamo",
            "bravo", "brava", "bravi", "brave", "bravissimo", "bravissima",
            "ottimo", "ottima", "perfetto", "perfetta", "gentile", "gentilissimo",
            "gentilissima", "gentilissimi", "utile", "utilissimo", "utilissima",
            "preciso", "precisa", "chiarissimo", "chiarissima", "fantastico", "fantastica",
            "eccellente", "complimenti", "grande", "top", "super",
        },
        "en": {
            "thanks", "thank", "thx", "great", "perfect", "awesome", "helpful", "excellent",
            "cool",
        },
    },
    "bye": {
        "it": {"arrivederci", "arrivederla", "addio", "presto", "prossima", "giornata",
               "serata", "notte", "buonanotte"},
        "en": {"bye", "goodbye"},
    },
    "greeting": {
        "it": {"ciao", "salve", "buongiorno", "buonasera", "giorno", "sera"},
        "en": {"hello", "hi", "hey", "morning", "afternoon", "evening"},
    },
}

# Parole di contorno: non fanno scattare nulla, ma non bastano a fare una domanda
_FILLERS = {
    # italiano
    "sei", "siete", "è", "e", "ed", "ma", "che", "uno", "una", "un", "proprio", "davvero",
    "veramente", "molto", "tanto", "tanti", "tante", "troppo", "assai", "mille", "infinite",
    "ok", "okay", "no", "va", "bene", "allora", "beh", "boh", "ah", "oh", "eh", "mah", "dai",
    "fai", "fa", "mi", "ti", "te", "tu", "voi", "a", "ci", "vediamo", "buona", "buon",
    "tutto", "tutti", "per", "l", "il", "lo", "la", "le", "i", "gli", "di", "del", "dello",
    "della", "dell", "da", "dal", "alla", "al", "risposta", "risposte", "aiuto",
    "informazioni", "info", "zautte", "assistente", "bot", "chatbot",
    # inglese
    "you", "are", "so", "very", "much", "really", "an", "is", "it", "this", "that",
    "all", "and", "for", "your", "the", "help", "see", "soon", "again", "good", "nice",
}

_WORDS: dict[str, tuple[str, str]] = {
    w: (kind, lang)
    for kind, by_lang in _TRIGGERS.items()
    for lang, words in by_lang.items()
    for w in words
}
_PRIORITY = ("insult", "thanks", "bye", "greeting")

_REPLIES = {
    "greeting": {
        "it": "Ciao! Scrivimi pure la tua domanda: cerco la risposta nelle pagine del sito.",
        "en": "Hello! Type your question and I will look for the answer in the website's pages.",
    },
    "thanks": {
        "it": "Grazie a te! Se hai altre domande, sono qui.",
        "en": "You're welcome! If you have other questions, I'm here.",
    },
    "bye": {
        "it": "Arrivederci! Se ti serve altro, sono qui.",
        "en": "Goodbye! If you need anything else, I'm here.",
    },
    "insult": {
        "it": ("Mi dispiace se la risposta non ti è stata utile. Prova a riformulare la "
               "domanda con qualche dettaglio in più; se una risposta è sbagliata, "
               "segnalala con 👎: le segnalazioni ci aiutano a migliorarla."),
        "en": ("I'm sorry if the answer wasn't helpful. Try rephrasing your question with "
               "a few more details; if an answer is wrong, report it with 👎: reports "
               "help us improve it."),
    },
}


def classify(text: str) -> tuple[str, str] | None:
    """(tipo, lingua) se il messaggio è solo cortesia o insulto, altrimenti None.
    Tipi: "insult", "thanks" (anche complimenti), "bye", "greeting"."""
    text = (text or "").strip().lower()
    if not text or len(text) > _MAX_CHARS:
        return None
    text = re.sub(r"(\w)\1{2,}", r"\1", text)   # «grazieee», «ciaooo»
    words = re.findall(r"\w+", text)
    if not words or len(words) > _MAX_WORDS:
        return None
    found: dict[str, set[str]] = {}
    for w in words:
        if w in _WORDS:
            kind, lang = _WORDS[w]
            found.setdefault(kind, set()).add(lang)
        elif w not in _FILLERS:
            return None   # parola di contenuto: è una domanda vera
    for kind in _PRIORITY:
        if kind in found:
            langs = {lang for ls in found.values() for lang in ls}
            return kind, ("en" if langs == {"en"} else "it")
    return None


def reply(kind: str, language: str = "it") -> str:
    """Risposta fissa per un messaggio riconosciuto da classify()."""
    texts = _REPLIES[kind]
    return texts.get(language, texts["it"])
