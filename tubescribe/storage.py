"""Saved transcripts and the library index.

    <output>/<collection>/<title> [<id>].<lang>.md
    <output>/.tubescribe/index.json
    <output>/.tubescribe/data/<uid>.json
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import threading
import unicodedata
from pathlib import Path
from typing import Any

from . import exporters
from . import text as T
from .models import Transcript

_ILLEGAL = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_RESERVED = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}


def safe_filename(name: str, max_length: int = 100) -> str:
    """Keeps non-Latin characters, removes characters Windows doesn't allow."""
    name = unicodedata.normalize("NFC", name or "")
    name = _ILLEGAL.sub(" ", name)
    name = re.sub(r"\s+", " ", name).strip(" .")
    if len(name) > max_length:
        name = name[:max_length].rsplit(" ", 1)[0].strip(" .") or name[:max_length]
    if not name or name.upper().split(".")[0] in _RESERVED:
        name = f"_{name}" if name else "untitled"
    return name


def request_signature(languages: list[str], allow_translation: bool, allow_auto: bool) -> str:
    """Part of the cache key, so asking for another language refetches."""
    raw = f"{','.join(languages)}|t{int(allow_translation)}|a{int(allow_auto)}"
    return hashlib.sha1(raw.encode()).hexdigest()[:8]


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tmp-", suffix=path.suffix)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(content)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


class Library:
    def __init__(self, root: str | os.PathLike[str]) -> None:
        self.root = Path(root).expanduser().resolve()
        self.meta_dir = self.root / ".tubescribe"
        self.data_dir = self.meta_dir / "data"
        self.index_path = self.meta_dir / "index.json"
        self._lock = threading.RLock()
        self._index: dict[str, dict[str, Any]] = self._load()

    def _load(self) -> dict[str, dict[str, Any]]:
        try:
            return json.loads(self.index_path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            return {}

    def _save(self) -> None:
        _atomic_write(self.index_path, json.dumps(self._index, ensure_ascii=False, indent=1))

    @staticmethod
    def uid(video_id: str, signature: str) -> str:
        return f"{video_id}-{signature}"

    def has(self, video_id: str, signature: str) -> bool:
        with self._lock:
            entry = self._index.get(self.uid(video_id, signature))
            return bool(entry) and (self.data_dir / f"{entry['uid']}.json").exists()

    def get(self, uid: str) -> dict[str, Any] | None:
        with self._lock:
            return self._index.get(uid)

    def entries(self, query: str = "") -> list[dict[str, Any]]:
        with self._lock:
            items = list(self._index.values())
        if query:
            q = query.casefold()
            items = [
                e for e in items
                if q in e["title"].casefold()
                or q in (e.get("channel") or "").casefold()
                or q in e.get("collection", "").casefold()
            ]
        return sorted(items, key=lambda e: e.get("fetched_at", ""), reverse=True)

    def save(
        self,
        transcript: Transcript,
        *,
        signature: str,
        formats: list[str],
        timestamps: bool = False,
        note: str | None = None,
    ) -> dict[str, Any]:
        v = transcript.video
        uid = self.uid(v.id, signature)
        folder = self.root / safe_filename(transcript.collection, 80)
        stem = f"{safe_filename(v.title)} [{v.id}].{transcript.language}"

        files: dict[str, str] = {}
        for fmt in formats:
            ext = exporters.FORMATS[fmt][0]
            path = folder / f"{stem}.{ext}"
            _atomic_write(path, exporters.render(transcript, fmt, timestamps=timestamps))
            files[fmt] = path.relative_to(self.root).as_posix()

        _atomic_write(self.data_dir / f"{uid}.json", json.dumps(transcript.to_dict(), ensure_ascii=False))

        body = T.full_text(transcript.segments, transcript.language)
        entry = {
            "uid": uid,
            "video_id": v.id,
            "title": v.title,
            "url": v.url,
            "channel": v.channel,
            "collection": transcript.collection,
            "upload_date": v.upload_date,
            "duration": v.duration,
            "thumbnail": v.thumbnail,
            "language": transcript.track.lang,
            "language_name": transcript.track.name,
            "kind": transcript.track.kind,
            "words": T.word_count(body, transcript.language),
            "preview": body[:220],
            "files": files,
            "fetched_at": transcript.fetched_at,
        }
        if note:
            entry["note"] = note
        with self._lock:
            self._index[uid] = entry
            self._save()
        return entry

    def load_transcript(self, uid: str) -> Transcript | None:
        path = self.data_dir / f"{uid}.json"
        if not re.fullmatch(r"[A-Za-z0-9_-]{11}-[0-9a-f]{8}", uid) or not path.exists():
            return None
        return Transcript.from_dict(json.loads(path.read_text(encoding="utf-8")))

    def resolve_file(self, relative: str) -> Path | None:
        path = (self.root / relative).resolve()
        return path if path.is_relative_to(self.root) and path.is_file() else None
