"""Tiny language guesser (English / Spanish / French) for the few canned lines the AI speaks without the model."""
from __future__ import annotations

import re

_ES = {"hola", "gracias", "quisiera", "tengo", "favor", "buenos", "buenas", "cita", "adios", "adiós", "para", "que", "qué",
       "con", "una", "el", "la", "mi", "su", "necesito", "estoy", "está", "sí", "si", "por", "grifo", "fuga", "casa", "tiene",
       "puede", "puedo", "dirección", "teléfono", "número", "nombre", "días", "dias", "hoy", "mañana", "cuando", "cuándo"}
_FR = {"bonjour", "bonsoir", "merci", "je", "j'ai", "voudrais", "besoin", "s'il", "plaît", "plait", "vous", "une", "avec", "mon",
       "ma", "est", "pas", "oui", "non", "revoir", "salut", "rendez-vous", "robinet", "fuite", "pour", "les", "des", "numéro",
       "nom", "adresse", "aujourd'hui", "demain", "quand", "peut", "pouvez", "c'est", "il", "elle", "jours"}

_STRONG_ES = {"hola", "gracias", "adiós", "adios", "buenos", "buenas"}
_STRONG_FR = {"bonjour", "bonsoir", "merci", "revoir", "s'il"}

_CLOSING = {
    "en": "Thank you for calling. Have a great day!",
    "es": "Gracias por llamar. ¡Que tenga un excelente día!",
    "fr": "Merci de votre appel. Bonne journée!",
}
_TRANSFER = {
    "en": "Let me connect you with someone now.",
    "es": "Permítame comunicarle con alguien ahora mismo.",
    "fr": "Je vous mets en relation avec quelqu'un tout de suite.",
}
_REPEAT = {
    "en": "Sorry, could you say that one more time?",
    "es": "Disculpe, ¿podría repetirlo una vez más?",
    "fr": "Désolé, pourriez-vous répéter une fois de plus?",
}


_FILLERS = {
    "en": ["One moment.", "Let me check that for you.", "Just a second."],
    "es": ["Un momento.", "Déjeme revisar eso.", "Solo un segundo."],
    "fr": ["Un instant.", "Je vérifie ça pour vous.", "Une seconde."],
}


def filler_line(lang: str, n: int = 0) -> str:
    lines = _FILLERS.get(lang, _FILLERS["en"])
    return lines[n % len(lines)]


def detect_language(text: str | None) -> str:
    """'en', 'es' or 'fr'. Defaults to English unless the text clearly looks Spanish or French."""
    words = re.findall(r"[\wÀ-ÿ'’-]+", (text or "").lower().replace("’", "'"))
    if not words:
        return "en"
    es = sum(1 for w in words if w in _ES) + sum(1 for w in words if w in _STRONG_ES) + (2 if re.search(r"[¿¡ñ]", text or "") else 0)
    fr = sum(1 for w in words if w in _FR) + sum(1 for w in words if w in _STRONG_FR) + (1 if re.search(r"[çêèàû]", text or "") else 0)
    if max(es, fr) >= 2 and es != fr:
        return "es" if es > fr else "fr"
    return "en"


def closing_line(lang: str) -> str:
    return _CLOSING.get(lang, _CLOSING["en"])


def transfer_line(lang: str) -> str:
    return _TRANSFER.get(lang, _TRANSFER["en"])


def repeat_line(lang: str) -> str:
    return _REPEAT.get(lang, _REPEAT["en"])
