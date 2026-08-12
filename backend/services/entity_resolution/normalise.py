from __future__ import annotations

import re
import unicodedata
from typing import Any

from backend.services.townland_service import canonical_name, normalize_townland_name

_BRACKET_RE = re.compile(r'\[.*?\]')
_PAREN_RE = re.compile(r'\(.*?\)')
_PLACEHOLDER_RE = re.compile(r'^[-\s.]+$')

_FORENAME_ABBREVIATIONS = {
    "JNO": "JOHN",
    "WM": "WILLIAM",
    "JAS": "JAMES",
    "THOS": "THOMAS",
    "MARGT": "MARGARET",
    "MICHL": "MICHAEL",
    "PATK": "PATRICK",
    "PAT": "PATRICK",
    "MATTW": "MATTHEW",
    "EDWD": "EDWARD",
}

_SURNAME_VARIANTS = {
    "MCDONNELL": "MCDONNELL",
    "MACDONNELL": "MCDONNELL",
    "MCCARTHY": "MCCARTHY",
    "MACCARTHY": "MCCARTHY",
    "OBRIEN": "OBRIEN",
    "O BRIEN": "OBRIEN",
    "O'BRIEN": "OBRIEN",
    "Ó BRIEN": "OBRIEN",
}


def clean_estate_name_field(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    text = _BRACKET_RE.sub('', text)
    text = _PAREN_RE.sub('', text)
    text = re.sub(r'\?+', '', text)
    text = re.sub(r'\s+', ' ', text).strip()
    if _PLACEHOLDER_RE.match(text) or not text:
        return ""
    return text


def normalise_text(value: Any) -> str:
    if value is None:
        return ""
    text = unicodedata.normalize("NFKD", str(value))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.replace("’", "'").replace("‘", "'")
    text = re.sub(r"[^A-Za-z0-9\s'-]", " ", text)
    text = re.sub(r"\s+", " ", text).strip().upper()
    return text


def _expand_forename(token: str) -> str:
    token = normalise_text(token)
    return _FORENAME_ABBREVIATIONS.get(token, token)


def _normalise_surname(token: str) -> str:
    token = normalise_text(token)
    token = token.replace("MC ", "MC").replace("MAC ", "MAC")
    if token.startswith("MAC"):
        token = "MC" + token[3:]
    token = token.replace("O ", "O").replace("O'", "O")
    token = _SURNAME_VARIANTS.get(token, token)
    return token


def phonetic_code(value: str) -> str:
    text = normalise_text(value)
    if not text:
        return ""
    try:
        import jellyfish

        return jellyfish.metaphone(text) or text
    except Exception:
        return text


_PLACEHOLDER_TOKEN_RE = re.compile(r'^[-\.]+$')


def split_person_name(raw_name: str, *, surname_first: bool = False) -> tuple[str, str]:
    tokens = [t for t in normalise_text(raw_name).split() if not _PLACEHOLDER_TOKEN_RE.match(t)]
    if not tokens:
        return "", ""
    if len(tokens) == 1:
        return "", _normalise_surname(tokens[0])
    if surname_first:
        surname = _normalise_surname(tokens[0])
        forename = " ".join(_expand_forename(tok) for tok in tokens[1:])
        return forename, surname
    surname = _normalise_surname(tokens[-1])
    forename = " ".join(_expand_forename(tok) for tok in tokens[:-1])
    return forename, surname


def normalise_person_fields(
    raw_name: str,
    *,
    forename: str | None = None,
    surname: str | None = None,
    surname_first: bool = False,
) -> dict[str, str]:
    nf = normalise_text(forename) if forename else ""
    ns = normalise_text(surname) if surname else ""
    if not (nf and ns):
        sf_forename, sf_surname = split_person_name(raw_name, surname_first=surname_first)
        nf = nf or sf_forename
        ns = ns or sf_surname
    nf = " ".join(_expand_forename(tok) for tok in nf.split())
    ns = _normalise_surname(ns)
    normalised_name = " ".join(x for x in [nf, ns] if x).strip()
    return {
        "normalised_name": normalised_name,
        "forename": nf,
        "surname": ns,
        "phonetic_forename": phonetic_code(nf),
        "phonetic_surname": phonetic_code(ns),
        "forename_initial": nf[:1] if nf else "",
    }


def normalise_place_name(raw_place: str | None) -> str:
    if not raw_place:
        return ""
    text = normalise_text(raw_place)
    text = re.sub(r"^TOWNLAND OF\s+", "", text).strip()
    text = re.sub(r"\bCIVIL PARISH\b", "", text).strip()
    text = re.sub(r"\bPARISH OF\b", "", text).strip()
    text = re.sub(r"\s+", " ", text).strip()
    canonical = canonical_name(text)
    return canonical or normalize_townland_name(text)
