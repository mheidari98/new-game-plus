"""Title normalisation and the numeric guard for fuzzy matching.

Store product names carry edition and platform noise that external
datasets do not:

    "The Witcher 3: Wild Hunt - Complete Edition"
    "EA SPORTS FC(tm) 26 Standard Edition PS4 & PS5"

Matching therefore happens on a stripped form. Nothing here does I/O.
"""

from __future__ import annotations

import re
import unicodedata

# Edition and platform noise. Removed before comparison so that an edition
# suffix cannot decide a match.
_STRIP_PATTERNS = [
    # "1-Year Anniversary Edition" would otherwise inject a stray "1" into the
    # numeric comparison and block the match against the base game.
    r"\b\d+\s*[-–]?\s*year anniversary(\s+edition)?\b",
    r"\bcross[- ]gen bundle\b",
    r"\bdigital extras\b",
    r"\bconsole edition\b",
    r"\bfor ps4 (?:and|&) ps5\b",
    r"\bps4 (?:and|&) ps5\b",
    r"\bps5 (?:and|&) ps4\b",
    r"\bplaystation ?[45]\b",
    r"\bps ?[45] version\b",
    r"\bps ?[45] edition\b",
    r"\bps ?[45]\b",
    r"\bdigital (?:deluxe|standard|edition)\b",
    r"\b(?:game of the year|goty|complete|definitive|director'?s cut|remastered|"
    r"enhanced|ultimate|legendary|gold|deluxe|premium|special|collector'?s|"
    r"anniversary|standard|bundle|collection)\s+(?:edition|bundle|pack)\b",
    r"\bedition\b",
    r"\(playstation plus\)",
    r"\bplaystation plus\b",
]
_STRIP_RE = [re.compile(p, re.I) for p in _STRIP_PATTERNS]

_PUNCT_RE = re.compile(r"[^\w\s]+")
_SPACE_RE = re.compile(r"\s+")
_SYMBOLS_RE = re.compile(r"[™®©℠]")
_NUMBER_RE = re.compile(r"\b\d+\b")

# Standalone roman numerals are folded to digits so that "Deliverance II" and
# "Deliverance 2" compare equal. Both sides of every comparison go through
# this, so a title where the numeral is really a letter (Mega Man X) still
# matches itself.
_ROMAN = {
    "i": "1", "ii": "2", "iii": "3", "iv": "4", "v": "5", "vi": "6",
    "vii": "7", "viii": "8", "ix": "9", "x": "10", "xi": "11", "xii": "12",
    "xiii": "13", "xiv": "14", "xv": "15", "xvi": "16",
}


def _despan(text: str) -> str:
    """Strip trademark symbols and curly quotes.

    Order matters: NFKD decomposes "™" into the letters "TM", which would
    turn "EA SPORTS FC™ 26" into "ea sports fctm 26". Symbols must go first.
    """
    text = _SYMBOLS_RE.sub(" ", text)
    text = text.replace("’", "'").replace("‘", "'")
    text = text.replace("“", '"').replace("”", '"')
    text = text.replace("–", " ").replace("—", " ")
    text = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in text if not unicodedata.combining(ch))


def normalize_title(name: str) -> str:
    """Aggressive form used as a matching key."""
    if not name:
        return ""
    text = _despan(name).lower()
    for pattern in _STRIP_RE:
        text = pattern.sub(" ", text)
    text = _PUNCT_RE.sub(" ", text)
    text = _SPACE_RE.sub(" ", text).strip()
    return " ".join(_ROMAN.get(tok, tok) for tok in text.split())


def numbers_compatible(a: str, b: str) -> bool:
    """Reject matches whose numbering differs -- the classic sequel trap.

    "Mortal Kombat 11" against "Mortal Kombat 1", or "Dying Light 2" against
    "Dying Light", score above any sane fuzzy threshold while being different
    games. Comparing the numeric tokens catches every such pair cheaply.

    This must gate *every* fuzzy match added to the project.
    """
    return sorted(_NUMBER_RE.findall(normalize_title(a))) == sorted(
        _NUMBER_RE.findall(normalize_title(b))
    )
