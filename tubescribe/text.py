"""Cleaning and paragraph splitting."""

from __future__ import annotations

import re
from dataclasses import dataclass

from .languages import uses_spaces
from .models import Segment

# [Music], [Applause], [音楽], ♪
_ANNOTATION = re.compile(r"\[[^\]]{1,40}\]|♪+|♫+")
_SPEAKER_CHANGE = re.compile(r"^\s*(>>|&gt;&gt;|-\s)")
_SPACE_BEFORE_PUNCT = re.compile(r"\s+([,.;:!?%)\]}»”،؛؟。、！？])")
_MULTI_SPACE = re.compile(r"[ \t]{2,}")

# Latin, Arabic, Urdu, Devanagari, CJK, Amharic
SENTENCE_END = (".", "!", "?", "…", "؟", "۔", "।", "॥", "。", "！", "？", "።")


@dataclass(frozen=True)
class Paragraph:
    start: float
    text: str


def clean_segments(segments: list[Segment], *, remove_annotations: bool = True) -> list[Segment]:
    out: list[Segment] = []
    for s in segments:
        text = s.text
        if remove_annotations:
            text = _ANNOTATION.sub(" ", text)
        text = _MULTI_SPACE.sub(" ", text).strip()
        if text:
            out.append(Segment(s.start, s.end, text))
    return out


def join(parts: list[str], lang: str | None) -> str:
    sep = " " if uses_spaces(lang) else ""
    text = sep.join(p.strip() for p in parts if p.strip())
    if sep:
        text = _SPACE_BEFORE_PUNCT.sub(r"\1", text)
    return _MULTI_SPACE.sub(" ", text).strip()


def _size(text: str, lang: str | None) -> int:
    return len(text.split()) if uses_spaces(lang) else int(len(text) / 2.5)


def paragraphs(
    segments: list[Segment],
    lang: str | None,
    *,
    target_words: int = 90,
    pause_seconds: float = 2.0,
) -> list[Paragraph]:
    """Split on sentence ends once a paragraph is long enough, on long pauses
    and speaker changes, and force a split when there is no punctuation."""
    result: list[Paragraph] = []
    buf: list[str] = []
    start = 0.0
    size = 0
    last_end = None

    def flush() -> None:
        nonlocal buf, size
        if buf:
            result.append(Paragraph(start, join(buf, lang)))
        buf, size = [], 0

    for seg in segments:
        text = seg.text
        speaker_change = bool(_SPEAKER_CHANGE.match(text))
        if speaker_change:
            text = _SPEAKER_CHANGE.sub("", text).strip()
        gap = seg.start - last_end if last_end is not None else 0.0

        if buf and (
            (speaker_change and size >= 12)
            or (gap >= pause_seconds and size >= target_words // 3)
            or size >= target_words * 2
        ):
            flush()

        if not buf:
            start = seg.start
        if text:
            buf.append(text)
            size += _size(text, lang)
        last_end = max(seg.end, seg.start)

        if size >= target_words and text.rstrip().endswith(SENTENCE_END):
            flush()

    flush()
    return result


def full_text(segments: list[Segment], lang: str | None) -> str:
    return join([s.text for s in segments], lang)


def word_count(text: str, lang: str | None) -> int:
    if uses_spaces(lang):
        return len(text.split())
    return len(re.sub(r"\s", "", text))  # count characters


def reading_minutes(text: str, lang: str | None) -> int:
    rate = 230 if uses_spaces(lang) else 500
    return max(1, round(word_count(text, lang) / rate))


def timestamp(seconds: float) -> str:
    seconds = int(seconds)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def srt_timestamp(seconds: float) -> str:
    ms = int(round(seconds * 1000))
    h, rem = divmod(ms, 3_600_000)
    m, rem = divmod(rem, 60_000)
    s, ms = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"
