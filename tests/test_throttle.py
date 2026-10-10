import random
import threading

import pytest

from tubescribe.throttle import AdaptiveThrottle, Cancelled, SpeedProfile

PROFILE = SpeedProfile("t", "T", 2.0, 4.0, 3, (50.0, 60.0), "")


class Recorder(AdaptiveThrottle):
    def __init__(self, **kw):
        self.now = 0.0
        super().__init__(PROFILE, rng=random.Random(1), clock=lambda: self.now, **kw)
        self.slept: list[tuple[float, str]] = []

    def sleep(self, seconds, reason=""):
        self.slept.append((seconds, reason))
        self.now += seconds


def test_first_request_is_immediate_then_jittered():
    t = Recorder()
    t.before_request()
    assert t.slept == []
    t.before_request()
    t.before_request()
    delays = [s for s, _ in t.slept]
    assert all(2.0 <= d <= 4.0 for d in delays)
    assert delays[0] != delays[1]


def test_batch_pause_every_n_requests():
    t = Recorder()
    for _ in range(4):
        t.before_request()
    assert t.slept[-1][0] >= 50 and "break" in t.slept[-1][1]


def test_elapsed_time_counts_toward_delay():
    t = Recorder()
    t.before_request()
    t.now += 100
    t.before_request()
    assert t.slept == []


def test_penalize_slows_down_and_success_recovers():
    t = Recorder()
    t.penalize()
    t.penalize()
    assert t.multiplier == 4.0
    for _ in range(50):
        t.success()
    assert t.multiplier == 1.0


def test_backoff_grows_exponentially():
    t = Recorder()
    t.backoff(0)
    t.backoff(3)
    assert t.slept[0][0] < t.slept[1][0]
    assert t.slept[1][0] >= 30 * 8 * 0.8


def test_real_sleep_is_cancellable():
    cancel = threading.Event()
    cancel.set()
    t = AdaptiveThrottle(PROFILE, cancel=cancel)
    with pytest.raises(Cancelled):
        t.sleep(30)
