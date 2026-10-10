from __future__ import annotations

import json

import pytest

from tubescribe.models import CaptionTrack, Segment, Source, VideoRef

JSON3_SAMPLE = json.dumps({
    "events": [
        {"tStartMs": 0, "dDurationMs": 20560, "id": 1},
        {"tStartMs": 160, "dDurationMs": 4080, "segs": [
            {"utf8": "Why"}, {"utf8": " do", "tOffsetMs": 240}, {"utf8": " you"},
            {"utf8": " get"}, {"utf8": " a"}, {"utf8": " brain"}, {"utf8": " freeze?"},
        ]},
        {"tStartMs": 2470, "dDurationMs": 1770, "aAppend": 1, "segs": [{"utf8": "\n"}]},
        {"tStartMs": 2480, "dDurationMs": 3440, "segs": [
            {"utf8": "That"}, {"utf8": " sudden"}, {"utf8": " &amp; intense"}, {"utf8": " pain."},
        ]},
    ]
})

# auto-generated VTT where each cue repeats the previous line
ROLLUP_VTT = """WEBVTT
Kind: captions
Language: en

00:00:00.160 --> 00:00:02.470 align:start position:0%
Why<00:00:00.400><c> do</c><00:00:00.560><c> you</c><00:00:00.719><c> get</c>

00:00:02.470 --> 00:00:02.480 align:start position:0%
Why do you get

00:00:02.480 --> 00:00:04.240 align:start position:0%
Why do you get
a<00:00:02.800><c> brain</c><00:00:03.120><c> freeze?</c>

00:00:04.240 --> 00:00:06.000 align:start position:0%
a brain freeze?
[Music]
"""


def make_info(*, manual=(), auto_orig=None, translated=("fr", "de"), language=None):
    def fmts(key, tlang=None):
        q = f"&tlang={tlang}" if tlang else ""
        return [
            {"ext": "json3", "url": f"https://yt/timedtext?lang={key}&fmt=json3{q}", "name": key},
            {"ext": "vtt", "url": f"https://yt/timedtext?lang={key}&fmt=vtt{q}", "name": key},
        ]

    auto = {}
    if auto_orig:
        auto[f"{auto_orig}-orig"] = fmts(f"{auto_orig}-orig")
        for t in translated:
            auto[t] = fmts(t, tlang=t)
    return {
        "id": "abcdefghijk",
        "title": "Sample video",
        "channel": "Sample channel",
        "upload_date": "20250102",
        "duration": 20,
        "language": language,
        "subtitles": {k: fmts(k) for k in manual} | {"live_chat": [{"ext": "json", "url": "x"}]},
        "automatic_captions": auto,
    }


class FakeClient:
    def __init__(self, videos=3, failures=None, info_factory=None, refuse_translations=False):
        self.refuse_translations = refuse_translations
        self.track_calls: list[str] = []
        self.videos = [VideoRef(f"vid{i:08d}", f"Video {i}", f"https://y/{i}") for i in range(videos)]
        self.failures = failures or {}
        self.info_factory = info_factory or (lambda vid: {**make_info(auto_orig="en"), "id": vid})
        self.info_calls: list[str] = []

    def resolve(self, url, limit=None):
        return Source("playlist", "My playlist", self.videos[:limit] if limit else self.videos, channel="Chan")

    def video_info(self, vid):
        self.info_calls.append(vid)
        if vid in self.failures:
            exc = self.failures[vid]
            raise exc() if isinstance(exc, type) else exc
        return self.info_factory(vid)

    @staticmethod
    def tracks(info):
        from tubescribe.languages import list_tracks
        return list_tracks(info)

    @staticmethod
    def meta(info, tracks):
        from tubescribe.youtube import YouTubeClient
        return YouTubeClient.meta(info, tracks)

    def fetch_track(self, track: CaptionTrack):
        self.track_calls.append(track.key)
        if self.refuse_translations and track.kind == "translated":
            from tubescribe.youtube import RateLimited
            raise RateLimited("HTTP Error 429: Too Many Requests")
        return [Segment(0, 2, f"Hello from {track.key}."), Segment(2, 4, "Second line.")]


class NoSleepThrottle:
    def __init__(self):
        import threading
        self.cancel = threading.Event()
        self.on_wait = None
        self.penalties = 0

    def before_request(self): ...
    def success(self): ...
    def penalize(self): self.penalties += 1
    def backoff(self, attempt, reason=""): ...
    def sleep(self, seconds, reason=""): ...
    def micro_pause(self): ...


@pytest.fixture
def fake_client():
    return FakeClient()
