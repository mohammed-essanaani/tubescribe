"""Markdown, plain text, JSON and SRT output."""

from __future__ import annotations

import json
from collections.abc import Callable

from . import text as T
from .models import Transcript

KIND_LABELS = {
    "manual": "Creator captions",
    "auto": "Auto-generated",
    "translated": "Machine-translated",
}


def _yaml(value: object) -> str:
    # JSON strings are valid YAML
    return json.dumps(value, ensure_ascii=False)


def _stats(t: Transcript) -> tuple[str, int, int]:
    body = T.full_text(t.segments, t.language)
    return body, T.word_count(body, t.language), T.reading_minutes(body, t.language)


def to_markdown(t: Transcript, *, timestamps: bool = False) -> str:
    v = t.video
    _, words, minutes = _stats(t)
    front = {
        "title": v.title,
        "channel": v.channel,
        "url": v.url,
        "video_id": v.id,
        "upload_date": v.upload_date,
        "duration": T.timestamp(v.duration) if v.duration else None,
        "language": t.track.lang,
        "language_name": t.track.name,
        "captions": t.track.kind,
        "words": words,
        "fetched_at": t.fetched_at,
    }
    lines = ["---"]
    lines += [f"{k}: {_yaml(val)}" for k, val in front.items() if val is not None]
    lines += ["---", "", f"# {v.title}", ""]

    facts = [f"[Watch on YouTube]({v.url})"]
    if v.channel:
        facts.append(f"**{v.channel}**")
    if v.upload_date:
        facts.append(v.upload_date)
    facts.append(f"{t.track.name} · {KIND_LABELS[t.track.kind]}")
    facts.append(f"~{minutes} min read")
    lines += [" · ".join(facts), "", "---", ""]

    for p in T.paragraphs(t.segments, t.language):
        if timestamps:
            stamp = f"[{T.timestamp(p.start)}](https://youtu.be/{v.id}?t={int(p.start)})"
            lines.append(f"**{stamp}** {p.text}")
        else:
            lines.append(p.text)
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def to_text(t: Transcript, *, timestamps: bool = False) -> str:
    out = [t.video.title, t.video.url, ""]
    for p in T.paragraphs(t.segments, t.language):
        out.append(f"[{T.timestamp(p.start)}] {p.text}" if timestamps else p.text)
        out.append("")
    return "\n".join(out).rstrip() + "\n"


def to_srt(t: Transcript, *, timestamps: bool = True) -> str:
    blocks = []
    for i, s in enumerate(t.segments, start=1):
        end = max(s.end, s.start + 0.5)
        blocks.append(f"{i}\n{T.srt_timestamp(s.start)} --> {T.srt_timestamp(end)}\n{s.text}\n")
    return "\n".join(blocks)


def to_json(t: Transcript, *, timestamps: bool = True) -> str:
    body, words, minutes = _stats(t)
    data = t.to_dict()
    data["stats"] = {"words": words, "reading_minutes": minutes}
    data["text"] = body
    data["paragraphs"] = [
        {"start": round(p.start, 2), "text": p.text} for p in T.paragraphs(t.segments, t.language)
    ]
    return json.dumps(data, ensure_ascii=False, indent=2)


Renderer = Callable[..., str]

FORMATS: dict[str, tuple[str, str, Renderer]] = {
    # format: (extension, mime type, renderer)
    "md": ("md", "text/markdown", to_markdown),
    "txt": ("txt", "text/plain", to_text),
    "json": ("json", "application/json", to_json),
    "srt": ("srt", "application/x-subrip", to_srt),
}


def render(t: Transcript, fmt: str, *, timestamps: bool = False) -> str:
    try:
        _, _, fn = FORMATS[fmt]
    except KeyError:
        raise ValueError(f"Unknown format '{fmt}'. Choose from: {', '.join(FORMATS)}") from None
    return fn(t, timestamps=timestamps)
