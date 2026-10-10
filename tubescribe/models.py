"""Data classes."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Literal

TrackKind = Literal["manual", "auto", "translated"]


@dataclass(frozen=True)
class VideoRef:
    id: str
    title: str
    url: str
    duration: float | None = None


@dataclass
class Source:
    kind: Literal["video", "playlist", "channel"]
    title: str
    videos: list[VideoRef]
    channel: str | None = None


@dataclass(frozen=True)
class CaptionTrack:
    key: str  # yt-dlp key, e.g. en-orig
    lang: str  # normalized, e.g. en
    name: str
    kind: TrackKind
    urls: dict[str, str] = field(default_factory=dict, compare=False, hash=False)

    def to_dict(self) -> dict[str, Any]:
        return {"key": self.key, "lang": self.lang, "name": self.name, "kind": self.kind}


@dataclass(frozen=True)
class Segment:
    start: float
    end: float
    text: str


@dataclass
class VideoMeta:
    id: str
    title: str
    url: str
    channel: str | None = None
    channel_url: str | None = None
    upload_date: str | None = None  # ISO yyyy-mm-dd
    duration: float | None = None
    view_count: int | None = None
    original_language: str | None = None
    thumbnail: str | None = None


@dataclass
class Transcript:
    video: VideoMeta
    track: CaptionTrack
    segments: list[Segment]
    collection: str
    fetched_at: str

    @property
    def language(self) -> str:
        return self.track.lang

    def to_dict(self) -> dict[str, Any]:
        return {
            "video": asdict(self.video),
            "track": self.track.to_dict(),
            "collection": self.collection,
            "fetched_at": self.fetched_at,
            "segments": [[round(s.start, 3), round(s.end, 3), s.text] for s in self.segments],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Transcript:
        t = data["track"]
        return cls(
            video=VideoMeta(**data["video"]),
            track=CaptionTrack(key=t["key"], lang=t["lang"], name=t["name"], kind=t["kind"]),
            segments=[Segment(float(a), float(b), str(c)) for a, b, c in data["segments"]],
            collection=data["collection"],
            fetched_at=data["fetched_at"],
        )
