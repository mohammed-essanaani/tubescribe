"""Caption track listing and language selection.

Track kinds:
    manual      uploaded by the creator
    auto        speech recognition in the spoken language (yt-dlp key ends in -orig)
    translated  YouTube machine translation (tlang= in the URL)
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import Any

from .models import CaptionTrack

ORIGINAL = "orig"  # the language spoken in the video

# written without spaces between words
NO_SPACE_LANGS = frozenset({"zh", "ja", "th", "lo", "km", "my", "bo"})
RTL_LANGS = frozenset({"ar", "he", "iw", "fa", "ur", "ps", "yi", "dv", "sd", "ug", "ckb"})

_SKIP_KEYS = {"live_chat", "rechat"}


def normalize(code: str | None) -> str:
    """en-orig -> en, pt_BR -> pt-BR, EN -> en"""
    if not code:
        return ""
    code = code.strip().replace("_", "-")
    if code.endswith("-orig"):
        code = code[: -len("-orig")]
    head, _, tail = code.partition("-")
    return f"{head.lower()}-{tail}" if tail else head.lower()


def base(code: str | None) -> str:
    """pt-BR -> pt, zh-Hans -> zh"""
    return normalize(code).split("-", 1)[0]


def is_rtl(code: str | None) -> bool:
    return base(code) in RTL_LANGS


def uses_spaces(code: str | None) -> bool:
    return base(code) not in NO_SPACE_LANGS


def _urls(formats: Iterable[dict[str, Any]]) -> dict[str, str]:
    return {f["ext"]: f["url"] for f in formats if f.get("ext") and f.get("url")}


def list_tracks(info: dict[str, Any]) -> list[CaptionTrack]:
    """Manual tracks first, then automatic ones."""
    tracks: list[CaptionTrack] = []

    for key, formats in (info.get("subtitles") or {}).items():
        if key in _SKIP_KEYS or not formats:
            continue
        tracks.append(
            CaptionTrack(
                key=key,
                lang=normalize(key),
                name=formats[0].get("name") or key,
                kind="manual",
                urls=_urls(formats),
            )
        )

    auto = info.get("automatic_captions") or {}
    has_orig_marker = any(k.endswith("-orig") for k in auto)
    for key, formats in auto.items():
        if key in _SKIP_KEYS or not formats:
            continue
        urls = _urls(formats)
        translated = any("tlang=" in u for u in urls.values())
        if has_orig_marker:
            kind = "auto" if key.endswith("-orig") else "translated"
        else:
            kind = "translated" if translated else "auto"
        tracks.append(
            CaptionTrack(
                key=key,
                lang=normalize(key),
                name=formats[0].get("name") or key,
                kind=kind,
                urls=urls,
            )
        )
    return tracks


def original_language(info: dict[str, Any], tracks: Sequence[CaptionTrack]) -> str | None:
    for t in tracks:
        if t.kind == "auto":
            return t.lang
    if info.get("language"):
        return normalize(info["language"])
    manual = [t for t in tracks if t.kind == "manual"]
    return manual[0].lang if len(manual) == 1 else None


def _match(tracks: Iterable[CaptionTrack], lang: str, kind: str) -> CaptionTrack | None:
    """Exact code first, then same base language."""
    candidates = [t for t in tracks if t.kind == kind]
    want = normalize(lang)
    for t in candidates:
        if t.lang.lower() == want.lower():
            return t
    for t in candidates:
        if base(t.lang) == base(want):
            return t
    return None


def pick_track(
    tracks: Sequence[CaptionTrack],
    preferences: Sequence[str] = (ORIGINAL,),
    *,
    original: str | None = None,
    allow_auto: bool = True,
    allow_translation: bool = True,
) -> CaptionTrack | None:
    """For each preferred language try manual, then auto, then translated.
    If nothing matches, fall back to the original language."""
    if not tracks:
        return None

    kinds = ["manual"] + (["auto"] if allow_auto else [])
    prefs = [p for p in (normalize(p) for p in preferences) if p] or [ORIGINAL]

    for pref in prefs:
        lang = original if pref == ORIGINAL else pref
        if not lang:
            continue
        for kind in kinds:
            if found := _match(tracks, lang, kind):
                return found
        if allow_translation and pref != ORIGINAL and (found := _match(tracks, lang, "translated")):
            return found

    if original:
        for kind in kinds:
            if found := _match(tracks, original, kind):
                return found
    for kind in kinds:
        for t in tracks:
            if t.kind == kind:
                return t
    return None


# shown in the web UI; the CLI accepts any code
COMMON_LANGUAGES: list[tuple[str, str]] = [
    ("en", "English"), ("ar", "العربية · Arabic"), ("fr", "Français · French"),
    ("es", "Español · Spanish"), ("de", "Deutsch · German"), ("pt", "Português · Portuguese"),
    ("it", "Italiano · Italian"), ("nl", "Nederlands · Dutch"), ("ru", "Русский · Russian"),
    ("uk", "Українська · Ukrainian"), ("pl", "Polski · Polish"), ("tr", "Türkçe · Turkish"),
    ("fa", "فارسی · Persian"), ("ur", "اردو · Urdu"), ("he", "עברית · Hebrew"),
    ("hi", "हिन्दी · Hindi"), ("bn", "বাংলা · Bengali"), ("ta", "தமிழ் · Tamil"),
    ("id", "Bahasa Indonesia"), ("ms", "Bahasa Melayu · Malay"), ("vi", "Tiếng Việt · Vietnamese"),
    ("th", "ไทย · Thai"), ("zh-Hans", "简体中文 · Chinese (Simplified)"),
    ("zh-Hant", "繁體中文 · Chinese (Traditional)"), ("ja", "日本語 · Japanese"),
    ("ko", "한국어 · Korean"), ("sv", "Svenska · Swedish"), ("no", "Norsk · Norwegian"),
    ("da", "Dansk · Danish"), ("fi", "Suomi · Finnish"), ("el", "Ελληνικά · Greek"),
    ("cs", "Čeština · Czech"), ("ro", "Română · Romanian"), ("hu", "Magyar · Hungarian"),
    ("sw", "Kiswahili · Swahili"), ("am", "አማርኛ · Amharic"), ("tl", "Filipino"),
]
