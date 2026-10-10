from tubescribe.pipeline import JobOptions, Pipeline
from tubescribe.storage import Library
from tubescribe.youtube import BotCheck, RateLimited, Unavailable, classify

from .conftest import FakeClient, NoSleepThrottle, make_info


def run(client, tmp_path, **opts):
    events = []
    p = Pipeline(client, Library(tmp_path), emit=events.append, throttle=NoSleepThrottle())
    summary = p.run(JobOptions(url="x", **opts))
    return summary, events


def test_saves_every_video_and_emits_lifecycle(tmp_path):
    summary, events = run(FakeClient(videos=3), tmp_path)
    assert summary.saved == 3 and summary.failed == 0
    types = [e["type"] for e in events]
    assert types[0] == "status" and types[1] == "resolved" and types[-1] == "finished"
    assert types.count("video_done") == 3


def test_second_run_skips_cached_videos_without_requests(tmp_path):
    run(FakeClient(videos=3), tmp_path)
    client = FakeClient(videos=4)
    summary, _ = run(client, tmp_path)
    assert summary.skipped == 3 and summary.saved == 1
    assert client.info_calls == ["vid00000003"]


def test_force_and_new_language_refetch(tmp_path):
    run(FakeClient(videos=2), tmp_path)
    assert run(FakeClient(videos=2), tmp_path, force=True)[0].saved == 2
    assert run(FakeClient(videos=2), tmp_path, languages=["fr"])[0].saved == 2


def test_unavailable_video_does_not_stop_job(tmp_path):
    client = FakeClient(videos=3, failures={"vid00000001": Unavailable("Private video")})
    summary, _ = run(client, tmp_path)
    assert (summary.saved, summary.failed) == (2, 1)


def test_rate_limit_retries_then_circuit_breaker_stops_job(tmp_path):
    failures = {f"vid{i:08d}": RateLimited("429") for i in range(5)}
    client = FakeClient(videos=5, failures=failures)
    summary, events = run(client, tmp_path)
    assert summary.stopped_reason and "rate limiting" in summary.stopped_reason
    assert summary.failed == 2
    assert len(client.info_calls) == 6  # 2 videos x 3 attempts
    assert any(e["type"] == "error" for e in events)


def test_no_captions_is_reported_separately(tmp_path):
    client = FakeClient(videos=1, info_factory=lambda vid: {**make_info(), "id": vid})
    summary, events = run(client, tmp_path)
    assert summary.no_captions == 1 and summary.failed == 0


def test_language_preference_flows_through(tmp_path):
    _, events = run(FakeClient(videos=1), tmp_path, languages=["de"])
    done = next(e for e in events if e["type"] == "video_done")
    assert done["entry"]["language"] == "de" and done["entry"]["kind"] == "translated"


def test_refused_translation_falls_back_to_original_without_retrying(tmp_path):
    client = FakeClient(videos=2, refuse_translations=True)
    throttle = NoSleepThrottle()
    events = []
    summary = Pipeline(client, Library(tmp_path), emit=events.append, throttle=throttle).run(
        JobOptions(url="x", languages=["fr"])
    )
    assert summary.saved == 2 and summary.failed == 0 and summary.stopped_reason is None
    assert client.track_calls == ["fr", "en-orig", "fr", "en-orig"]
    assert throttle.penalties == 0
    done = [e["entry"] for e in events if e["type"] == "video_done"]
    assert done[0]["language"] == "en" and "translation" in done[0]["note"]


def test_classify_maps_yt_dlp_messages():
    assert isinstance(classify(Exception("HTTP Error 429: Too Many Requests")), RateLimited)
    assert isinstance(classify(Exception("Sign in to confirm you’re not a bot")), BotCheck)
    assert isinstance(classify(Exception("ERROR: [youtube] abcdefghijk: Private video")), Unavailable)
    assert str(classify(Exception("ERROR: [youtube] abcdefghijk: Private video"))) == "Private video"
