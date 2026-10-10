"""Caption parsers. json3 is preferred; VTT is a fallback."""

from __future__ import annotations

import html
import json
import re

from .models import Segment

_TAG = re.compile(r"<[^>]+>")
_WS = re.compile(r"\s+")
_VTT_TIME = re.compile(
    r"(?P<s>(?:\d+:)?\d{1,2}:\d{2}[.,]\d{3})\s+-->\s+(?P<e>(?:\d+:)?\d{1,2}:\d{2}[.,]\d{3})"
)


def _clean(text: str) -> str:
    return _WS.sub(" ", html.unescape(_TAG.sub("", text))).strip()


def parse_json3(payload: str | bytes) -> list[Segment]:
    data = json.loads(payload)
    segments: list[Segment] = []
    for event in data.get("events", []):
        segs = event.get("segs")
        if not segs:
            continue
        text = _clean("".join(s.get("utf8", "") for s in segs))
        if not text:
            continue
        start = event.get("tStartMs", 0) / 1000
        end = start + event.get("dDurationMs", 0) / 1000
        segments.append(Segment(start, end, text))
    return segments


def _vtt_seconds(stamp: str) -> float:
    parts = stamp.replace(",", ".").split(":")
    seconds = float(parts[-1])
    for i, p in enumerate(reversed(parts[:-1]), start=1):
        seconds += int(p) * 60**i
    return seconds


def parse_vtt(payload: str | bytes) -> list[Segment]:
    if isinstance(payload, bytes):
        payload = payload.decode("utf-8", errors="replace")

    segments: list[Segment] = []
    previous_lines: list[str] = []
    lines = payload.splitlines()
    i = 0
    while i < len(lines):
        match = _VTT_TIME.search(lines[i])
        i += 1
        if not match:
            continue
        cue: list[str] = []
        while i < len(lines) and lines[i].strip():
            cue.append(_clean(lines[i]))
            i += 1
        cue = [c for c in cue if c]
        # auto captions repeat the previous cue's lines
        fresh = [c for c in cue if c not in previous_lines]
        if cue:
            previous_lines = cue
        if fresh:
            segments.append(
                Segment(_vtt_seconds(match["s"]), _vtt_seconds(match["e"]), " ".join(fresh))
            )
    return segments


def parse(payload: str | bytes, ext: str) -> list[Segment]:
    if ext == "json3":
        return parse_json3(payload)
    if ext == "vtt":
        return parse_vtt(payload)
    raise ValueError(f"Unsupported caption format: {ext}")


PREFERRED_FORMATS = ("json3", "vtt")
