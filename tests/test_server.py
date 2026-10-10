import time

from fastapi.testclient import TestClient

from tubescribe import server

from .conftest import FakeClient


def make_app(tmp_path, monkeypatch):
    monkeypatch.setattr(server, "YouTubeClient", lambda *_: FakeClient(videos=2))
    monkeypatch.setattr("tubescribe.throttle.AdaptiveThrottle.sleep", lambda *a, **k: None)
    return TestClient(server.create_app(tmp_path))


def test_config_and_index(tmp_path, monkeypatch):
    client = make_app(tmp_path, monkeypatch)
    cfg = client.get("/api/config").json()
    assert {"md", "txt", "json", "srt"} <= set(cfg["formats"])
    assert any(lang["code"] == "ar" for lang in cfg["languages"])
    assert "TubeScribe" in client.get("/").text
    assert client.get("/static/app.js").status_code == 200


def test_job_lifecycle_and_library(tmp_path, monkeypatch):
    client = make_app(tmp_path, monkeypatch)
    job = client.post("/api/jobs", json={"url": "https://youtube.com/@x", "formats": ["md", "srt"]})
    assert job.status_code == 201
    job_id = job.json()["id"]

    with client.stream("GET", f"/api/jobs/{job_id}/events") as stream:
        body = "".join(stream.iter_text())
    assert '"type": "finished"' in body

    lib = client.get("/api/library").json()
    assert lib["count"] == 2
    uid = lib["items"][0]["uid"]

    reader = client.get(f"/api/library/{uid}").json()
    assert reader["paragraphs"] and reader["rtl"] is False

    dl = client.get(f"/api/library/{uid}/download?format=srt")
    assert dl.status_code == 200 and "-->" in dl.text
    assert "attachment" in dl.headers["content-disposition"]

    z = client.get("/api/export.zip?format=txt")
    assert z.status_code == 200 and z.content[:2] == b"PK"


def test_rejects_bad_input_and_concurrent_jobs(tmp_path, monkeypatch):
    client = make_app(tmp_path, monkeypatch)
    assert client.post("/api/jobs", json={"url": "x", "formats": ["exe"]}).status_code == 422
    assert client.post("/api/jobs", json={"url": "x", "speed": "ludicrous"}).status_code == 422
    assert client.get("/api/library/../../etc/passwd").status_code == 404

    monkeypatch.setattr("tubescribe.pipeline.Pipeline.run", lambda self, o: time.sleep(0.5))
    assert client.post("/api/jobs", json={"url": "aa"}).status_code == 201
    assert client.post("/api/jobs", json={"url": "bb"}).status_code == 409
