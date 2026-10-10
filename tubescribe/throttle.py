"""Random delays between videos, longer breaks every batch, and backoff on 429s."""

from __future__ import annotations

import random
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass


@dataclass(frozen=True)
class SpeedProfile:
    name: str
    label: str
    min_delay: float
    max_delay: float
    batch_size: int
    batch_pause: tuple[float, float]
    description: str


PROFILES: dict[str, SpeedProfile] = {
    p.name: p
    for p in (
        SpeedProfile(
            "careful", "Careful", 5.0, 11.0, 15, (60.0, 150.0),
            "Slowest and safest. Best for large channels or a shared IP.",
        ),
        SpeedProfile(
            "balanced", "Balanced", 2.5, 6.0, 30, (30.0, 75.0),
            "Recommended. Natural pacing with periodic breaks.",
        ),
        SpeedProfile(
            "fast", "Fast", 1.0, 2.5, 60, (15.0, 30.0),
            "For a handful of videos. Higher risk on big batches.",
        ),
    )
}
DEFAULT_PROFILE = "balanced"

MAX_MULTIPLIER = 8.0
MAX_BACKOFF = 15 * 60.0

WaitCallback = Callable[[float, str], None]


class Cancelled(Exception):
    pass


class AdaptiveThrottle:
    def __init__(
        self,
        profile: SpeedProfile | str = DEFAULT_PROFILE,
        *,
        cancel: threading.Event | None = None,
        on_wait: WaitCallback | None = None,
        rng: random.Random | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.profile = PROFILES[profile] if isinstance(profile, str) else profile
        self.cancel = cancel or threading.Event()
        self.on_wait = on_wait
        self.rng = rng or random.Random()
        self.clock = clock
        self.multiplier = 1.0
        self.requests = 0
        self._last: float | None = None

    def before_request(self) -> None:
        if self._last is not None:
            p = self.profile
            if self.requests and self.requests % p.batch_size == 0:
                pause = self.rng.uniform(*p.batch_pause) * self.multiplier
                self.sleep(pause, f"Taking a short break after {self.requests} videos")
            else:
                delay = self.rng.uniform(p.min_delay, p.max_delay) * self.multiplier
                elapsed = self.clock() - self._last
                if delay > elapsed:
                    self.sleep(delay - elapsed, "Pacing requests")
        self.requests += 1
        self._last = self.clock()

    def success(self) -> None:
        self.multiplier = max(1.0, self.multiplier * 0.85)

    def penalize(self) -> None:
        self.multiplier = min(MAX_MULTIPLIER, self.multiplier * 2)

    def backoff(self, attempt: int, reason: str = "YouTube asked us to slow down") -> None:
        # ~30s, 60s, 120s with jitter
        delay = min(MAX_BACKOFF, 30.0 * (2**attempt)) * self.rng.uniform(0.8, 1.3)
        self.sleep(delay, reason)

    def micro_pause(self) -> None:
        self.sleep(self.rng.uniform(0.4, 1.2))

    def sleep(self, seconds: float, reason: str = "") -> None:
        if seconds <= 0:
            return
        if self.on_wait and seconds >= 1:
            self.on_wait(seconds, reason)
        if self.cancel.wait(seconds):
            raise Cancelled()
