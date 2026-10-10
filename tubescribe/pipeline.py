"""Runs a job and reports progress through emit(event). Used by both the CLI and the server."""

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from . import text as T
from .languages import ORIGINAL, pick_track
from .models import Transcript, VideoRef
from .storage import Library, request_signature
from .throttle import DEFAULT_PROFILE, AdaptiveThrottle, Cancelled
from .youtube import BotCheck, RateLimited, YouTubeClient, YouTubeError, classify

Event = dict[str, Any]
Emit = Callable[[Event], None]

MAX_ATTEMPTS = 3
CIRCUIT_BREAKER = 2  # stop after this many rate-limited videos in a row


@dataclass
class JobOptions:
    url: str
    languages: list[str] = field(default_factory=lambda: [ORIGINAL])
    allow_translation: bool = True
    allow_auto: bool = True
    formats: list[str] = field(default_factory=lambda: ["md"])
    timestamps: bool = False
    limit: int | None = None
    speed: str = DEFAULT_PROFILE
    force: bool = False

    @property
    def signature(self) -> str:
        return request_signature(self.languages, self.allow_translation, self.allow_auto)


@dataclass
class JobSummary:
    total: int = 0
    saved: int = 0
    skipped: int = 0
    no_captions: int = 0
    failed: int = 0
    stopped_reason: str | None = None
    cancelled: bool = False

    def as_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)


class StopJob(Exception):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


class Pipeline:
    def __init__(
        self,
        client: YouTubeClient,
        library: Library,
        *,
        emit: Emit | None = None,
        cancel: threading.Event | None = None,
        throttle: AdaptiveThrottle | None = None,
    ) -> None:
        self.client = client
        self.library = library
        self.emit: Emit = emit or (lambda e: None)
        self.cancel = cancel or threading.Event()
        self._throttle = throttle

    def _on_wait(self, seconds: float, reason: str) -> None:
        self.emit({"type": "waiting", "seconds": round(seconds, 1), "reason": reason})

    def run(self, opts: JobOptions) -> JobSummary:
        summary = JobSummary()
        throttle = self._throttle or AdaptiveThrottle(opts.speed, cancel=self.cancel, on_wait=self._on_wait)
        throttle.cancel = self.cancel
        throttle.on_wait = throttle.on_wait or self._on_wait

        try:
            self.emit({"type": "status", "message": "Resolving link..."})
            source = self.client.resolve(opts.url, opts.limit)
            videos = source.videos[: opts.limit] if opts.limit else source.videos
            summary.total = len(videos)
            collection = source.title if source.kind != "video" else (source.channel or "Videos")
            self.emit({
                "type": "resolved",
                "kind": source.kind,
                "title": source.title,
                "channel": source.channel,
                "total": len(videos),
            })

            rate_limit_strikes = 0
            for index, ref in enumerate(videos, start=1):
                if self.cancel.is_set():
                    raise Cancelled()
                base = {"index": index, "total": len(videos), "video_id": ref.id, "title": ref.title}

                if not opts.force and self.library.has(ref.id, opts.signature):
                    summary.skipped += 1
                    self.emit({**base, "type": "video_skipped", "reason": "Already in library"})
                    continue

                self.emit({**base, "type": "video_start"})
                try:
                    entry = self._process(ref, opts, collection, throttle)
                    rate_limit_strikes = 0
                except _NoCaptions as exc:
                    summary.no_captions += 1
                    self.emit({**base, "type": "video_no_captions", "reason": str(exc)})
                    continue
                except (RateLimited, BotCheck) as exc:
                    summary.failed += 1
                    rate_limit_strikes += 1
                    self.emit({**base, "type": "video_failed", "reason": str(exc), "error": exc.kind})
                    if rate_limit_strikes >= CIRCUIT_BREAKER:
                        raise StopJob(
                            "Stopped because YouTube keeps rate limiting requests. Try again later "
                            "with the careful speed or browser cookies. Saved videos will be skipped."
                        ) from exc
                    continue
                except YouTubeError as exc:
                    summary.failed += 1
                    self.emit({**base, "type": "video_failed", "reason": str(exc), "error": exc.kind})
                    continue

                summary.saved += 1
                self.emit({**base, "type": "video_done", "entry": entry})

        except Cancelled:
            summary.cancelled = True
        except StopJob as exc:
            summary.stopped_reason = str(exc)
            self.emit({"type": "error", "message": str(exc)})
        except YouTubeError as exc:
            summary.stopped_reason = str(exc)
            self.emit({"type": "error", "message": str(exc)})

        self.emit({"type": "finished", "summary": summary.as_dict()})
        return summary

    def _process(
        self, ref: VideoRef, opts: JobOptions, collection: str, throttle: AdaptiveThrottle
    ) -> dict[str, Any]:
        throttle.before_request()
        info = self._call(lambda: self.client.video_info(ref.id), throttle)
        tracks = self.client.tracks(info)
        meta = self.client.meta(info, tracks)
        track = pick_track(
            tracks,
            opts.languages,
            original=meta.original_language,
            allow_auto=opts.allow_auto,
            allow_translation=opts.allow_translation,
        )
        if track is None:
            throttle.success()
            raise _NoCaptions("No captions available for this video.")

        throttle.micro_pause()
        note = None
        try:
            # translated tracks get 429s much more often than normal ones,
            # so don't retry them, just use the original language
            raw = self._call(
                lambda: self.client.fetch_track(track), throttle, retry_limits=track.kind != "translated"
            )
        except (RateLimited, BotCheck):
            fallback = (
                pick_track(tracks, [ORIGINAL], original=meta.original_language,
                           allow_auto=opts.allow_auto, allow_translation=False)
                if track.kind == "translated" else None
            )
            if fallback is None:
                raise
            note = f"{track.name} translation unavailable, saved {fallback.name} instead."
            self.emit({"type": "status", "message": note})
            track = fallback
            throttle.micro_pause()
            raw = self._call(lambda: self.client.fetch_track(track), throttle)

        segments = T.clean_segments(raw)
        throttle.success()
        if not segments:
            raise _NoCaptions("Caption track was empty.")

        transcript = Transcript(
            video=meta, track=track, segments=segments, collection=collection, fetched_at=_now()
        )
        return self.library.save(
            transcript, signature=opts.signature, formats=opts.formats,
            timestamps=opts.timestamps, note=note,
        )

    @staticmethod
    def _call(fn: Callable[[], Any], throttle: AdaptiveThrottle, *, retry_limits: bool = True) -> Any:
        for attempt in range(MAX_ATTEMPTS):
            try:
                return fn()
            except Exception as exc:  # noqa: BLE001
                err = classify(exc)
                limited = isinstance(err, (RateLimited, BotCheck))
                if not err.retryable or attempt == MAX_ATTEMPTS - 1 or (limited and not retry_limits):
                    raise err from exc
            if limited:
                throttle.penalize()
                throttle.backoff(attempt, f"{err} Backing off before retrying")
            else:
                throttle.sleep(5 * (attempt + 1), "Network hiccup, retrying")
        raise YouTubeError("Unknown failure")


class _NoCaptions(Exception):
    pass
