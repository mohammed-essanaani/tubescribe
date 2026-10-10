"""yt-dlp wrapper."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any

import yt_dlp
from yt_dlp.utils import DownloadError, ExtractorError

from . import parsers
from .languages import list_tracks, original_language
from .models import CaptionTrack, Segment, Source, VideoMeta, VideoRef

VIDEO_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")
BARE_CHANNEL = re.compile(
    r"^https?://(?:www\.|m\.)?youtube\.com/(?:@[^/?#]+|channel/[^/?#]+|c/[^/?#]+|user/[^/?#]+)/?$"
)
_UNAVAILABLE_TITLES = {"[Private video]", "[Deleted video]"}


class YouTubeError(Exception):
    kind = "error"
    retryable = False


class RateLimited(YouTubeError):
    kind = "rate_limited"
    retryable = True


class BotCheck(YouTubeError):
    kind = "bot_check"
    retryable = True


class Unavailable(YouTubeError):
    kind = "unavailable"


class NetworkError(YouTubeError):
    kind = "network"
    retryable = True


def classify(exc: BaseException) -> YouTubeError:
    if isinstance(exc, YouTubeError):
        return exc
    msg = str(exc)
    low = msg.lower()
    cleaned = re.sub(r"^ERROR:\s*(\[[^\]]+\]\s*)?([\w-]{11}:\s*)?", "", msg).strip()
    if "429" in msg or "too many requests" in low:
        return RateLimited("YouTube is rate-limiting requests (HTTP 429).")
    if "not a bot" in low or "confirm you" in low and "bot" in low:
        return BotCheck("YouTube asked to confirm we're not a bot.")
    if any(
        s in low
        for s in (
            "private video", "video unavailable", "has been removed", "members-only",
            "join this channel", "confirm your age", "age-restricted", "premieres in",
            "this live event", "does not have a", "copyright",
        )
    ):
        return Unavailable(cleaned or msg)
    if any(s in low for s in ("timed out", "connection", "getaddrinfo", "ssl", "network")):
        return NetworkError(cleaned or msg)
    return YouTubeError(cleaned or msg)


class _QuietLogger:
    def debug(self, msg: str) -> None: ...
    def info(self, msg: str) -> None: ...
    def warning(self, msg: str) -> None: ...
    def error(self, msg: str) -> None: ...


@dataclass
class ClientOptions:
    cookies_file: str | None = None
    cookies_from_browser: str | None = None  # "firefox" or "chrome:Profile 1"
    proxy: str | None = None
    timeout: float = 20.0


def normalize_url(text: str) -> str:
    text = text.strip()
    if VIDEO_ID.match(text):
        return f"https://www.youtube.com/watch?v={text}"
    if text.startswith("@"):
        return f"https://www.youtube.com/{text}"
    if not re.match(r"^https?://", text) and ("youtube.com" in text or "youtu.be" in text):
        return "https://" + text
    return text


def _iso_date(value: str | None) -> str | None:
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y%m%d").date().isoformat()
    except ValueError:
        return value


class YouTubeClient:
    def __init__(self, options: ClientOptions | None = None) -> None:
        self.options = options or ClientOptions()
        self._base = self._build_params()

    def _build_params(self) -> dict[str, Any]:
        o = self.options
        params: dict[str, Any] = {
            "quiet": True,
            "no_warnings": True,
            "noprogress": True,
            "skip_download": True,
            "logger": _QuietLogger(),
            "socket_timeout": o.timeout,
            "retries": 2,
            "extractor_retries": 2,
            # captions only, skip stream manifests
            "extractor_args": {"youtube": {"skip": ["hls", "dash"]}},
        }
        if o.cookies_file:
            params["cookiefile"] = o.cookies_file
        if o.cookies_from_browser:
            browser, _, profile = o.cookies_from_browser.partition(":")
            params["cookiesfrombrowser"] = (browser.strip().lower(), profile or None, None, None)
        if o.proxy:
            params["proxy"] = o.proxy
        return params

    def _ydl(self, **extra: Any) -> yt_dlp.YoutubeDL:
        return yt_dlp.YoutubeDL({**self._base, **extra})

    def resolve(self, url: str, limit: int | None = None) -> Source:
        url = normalize_url(url)
        if BARE_CHANNEL.match(url):
            return self._resolve_channel(url.rstrip("/"), limit)

        info = self._flat(url, limit)
        if info.get("_type") in (None, "video") or "entries" not in info:
            ref = VideoRef(
                id=info["id"],
                title=info.get("title") or info["id"],
                url=info.get("webpage_url") or f"https://www.youtube.com/watch?v={info['id']}",
                duration=info.get("duration"),
            )
            return Source("video", ref.title, [ref], channel=info.get("channel") or info.get("uploader"))

        videos = self._collect(info, limit)
        kind = "channel" if info.get("channel_id") and info.get("id") == info.get("channel_id") else "playlist"
        return Source(kind, info.get("title") or "Playlist", videos, channel=info.get("channel") or info.get("uploader"))

    def _resolve_channel(self, url: str, limit: int | None) -> Source:
        # channel root: get both the videos and shorts tabs
        videos: list[VideoRef] = []
        channel = None
        for tab in ("videos", "shorts"):
            remaining = None if limit is None else limit - len(videos)
            if remaining is not None and remaining <= 0:
                break
            try:
                info = self._flat(f"{url}/{tab}", remaining)
            except YouTubeError as exc:
                if isinstance(exc, Unavailable):  # no shorts tab
                    continue
                raise
            channel = channel or info.get("channel") or info.get("uploader")
            seen = {v.id for v in videos}
            videos += [v for v in self._collect(info, remaining) if v.id not in seen]
        if not videos and channel is None:
            raise Unavailable("This channel has no public videos.")
        return Source("channel", channel or "Channel", videos, channel=channel)

    def _flat(self, url: str, limit: int | None) -> dict[str, Any]:
        extra: dict[str, Any] = {"extract_flat": "in_playlist"}
        if limit:
            extra["playlistend"] = limit
        try:
            with self._ydl(**extra) as ydl:
                info = ydl.extract_info(url, download=False)
        except (DownloadError, ExtractorError) as exc:
            raise classify(exc) from exc
        if not info:
            raise Unavailable("Nothing found at this link.")
        return info

    def _collect(self, info: dict[str, Any], limit: int | None, depth: int = 0) -> list[VideoRef]:
        out: list[VideoRef] = []
        seen: set[str] = set()
        for entry in info.get("entries") or []:
            if limit is not None and len(out) >= limit:
                break
            if not entry:
                continue
            is_nested = entry.get("_type") == "playlist" or entry.get("ie_key") == "YoutubeTab"
            if is_nested and depth < 2:
                nested = entry if entry.get("entries") is not None else self._flat(entry["url"], limit)
                for ref in self._collect(nested, None if limit is None else limit - len(out), depth + 1):
                    if ref.id not in seen:
                        seen.add(ref.id)
                        out.append(ref)
                continue
            vid = entry.get("id")
            title = entry.get("title") or vid
            if not vid or not VIDEO_ID.match(vid) or title in _UNAVAILABLE_TITLES or vid in seen:
                continue
            seen.add(vid)
            out.append(
                VideoRef(
                    id=vid,
                    title=title,
                    url=f"https://www.youtube.com/watch?v={vid}",
                    duration=entry.get("duration"),
                )
            )
        return out

    def video_info(self, video_id_or_url: str) -> dict[str, Any]:
        url = normalize_url(video_id_or_url)
        try:
            with self._ydl() as ydl:
                # process=False skips format selection, which isn't needed
                info = ydl.extract_info(url, download=False, process=False)
        except (DownloadError, ExtractorError) as exc:
            raise classify(exc) from exc
        if not info:
            raise Unavailable("Video not found.")
        return info

    @staticmethod
    def meta(info: dict[str, Any], tracks: list[CaptionTrack]) -> VideoMeta:
        return VideoMeta(
            id=info["id"],
            title=info.get("title") or info["id"],
            url=info.get("webpage_url") or f"https://www.youtube.com/watch?v={info['id']}",
            channel=info.get("channel") or info.get("uploader"),
            channel_url=info.get("channel_url") or info.get("uploader_url"),
            upload_date=_iso_date(info.get("upload_date")),
            duration=info.get("duration"),
            view_count=info.get("view_count"),
            original_language=original_language(info, tracks),
            thumbnail=info.get("thumbnail"),
        )

    @staticmethod
    def tracks(info: dict[str, Any]) -> list[CaptionTrack]:
        return list_tracks(info)

    def fetch_track(self, track: CaptionTrack) -> list[Segment]:
        for ext in parsers.PREFERRED_FORMATS:
            if url := track.urls.get(ext):
                try:
                    with self._ydl() as ydl:
                        payload = ydl.urlopen(url).read()
                except Exception as exc:  # noqa: BLE001
                    raise classify(exc) from exc
                if not payload:
                    continue
                return parsers.parse(payload, ext)
        raise YouTubeError(f"No supported caption format for track {track.key}.")
