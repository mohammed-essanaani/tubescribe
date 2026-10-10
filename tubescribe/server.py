"""FastAPI server for the web UI. Only one job runs at a time."""

from __future__ import annotations

import asyncio
import io
import json
import os
import subprocess
import sys
import threading
import uuid
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator

from . import __version__, exporters
from .languages import COMMON_LANGUAGES, ORIGINAL, is_rtl
from .pipeline import JobOptions, Pipeline
from .storage import Library, safe_filename
from .throttle import DEFAULT_PROFILE, PROFILES
from .youtube import ClientOptions, YouTubeClient, YouTubeError

WEB_DIR = Path(__file__).parent / "web"


class JobRequest(BaseModel):
    url: str = Field(min_length=2, max_length=500)
    languages: list[str] = Field(default_factory=lambda: [ORIGINAL], max_length=5)
    allow_translation: bool = True
    allow_auto: bool = True
    formats: list[str] = Field(default_factory=lambda: ["md"], min_length=1)
    timestamps: bool = False
    limit: int | None = Field(default=None, ge=1, le=5000)
    speed: str = DEFAULT_PROFILE
    force: bool = False

    @field_validator("formats")
    @classmethod
    def _formats(cls, v: list[str]) -> list[str]:
        bad = [f for f in v if f not in exporters.FORMATS]
        if bad:
            raise ValueError(f"Unknown formats: {bad}")
        return list(dict.fromkeys(v))

    @field_validator("speed")
    @classmethod
    def _speed(cls, v: str) -> str:
        if v not in PROFILES:
            raise ValueError(f"speed must be one of {list(PROFILES)}")
        return v


@dataclass
class Job:
    id: str
    options: JobOptions
    events: list[dict[str, Any]] = field(default_factory=list)
    cancel: threading.Event = field(default_factory=threading.Event)
    done: bool = False
    lock: threading.Lock = field(default_factory=threading.Lock)

    def push(self, event: dict[str, Any]) -> None:
        with self.lock:
            event["seq"] = len(self.events)
            self.events.append(event)
            if event["type"] == "finished":
                self.done = True

    def since(self, seq: int) -> list[dict[str, Any]]:
        with self.lock:
            return self.events[seq:]


def create_app(output_dir: str | os.PathLike[str], client_options: ClientOptions | None = None) -> FastAPI:
    app = FastAPI(title="TubeScribe", version=__version__, docs_url="/api/docs", redoc_url=None)
    library = Library(output_dir)
    client = YouTubeClient(client_options)
    jobs: dict[str, Job] = {}
    state: dict[str, Job | None] = {"active": None}

    def active() -> Job | None:
        job = state["active"]
        return job if job and not job.done else None

    @app.get("/api/config")
    def config() -> dict[str, Any]:
        return {
            "version": __version__,
            "output_dir": str(library.root),
            "languages": [{"code": c, "name": n} for c, n in COMMON_LANGUAGES],
            "formats": list(exporters.FORMATS),
            "speeds": [
                {"name": p.name, "label": p.label, "description": p.description}
                for p in PROFILES.values()
            ],
            "default_speed": DEFAULT_PROFILE,
            "active_job": active().id if active() else None,
        }

    @app.get("/api/tracks")
    def tracks(url: str = Query(min_length=2, max_length=500)) -> dict[str, Any]:
        try:
            info = client.video_info(url)
        except YouTubeError as exc:
            raise HTTPException(422, str(exc)) from exc
        all_tracks = client.tracks(info)
        meta = client.meta(info, all_tracks)
        return {
            "video": {"id": meta.id, "title": meta.title, "channel": meta.channel, "thumbnail": meta.thumbnail},
            "original_language": meta.original_language,
            "tracks": [t.to_dict() for t in all_tracks if t.kind != "translated"],
            "translatable": any(t.kind == "translated" for t in all_tracks),
        }

    @app.post("/api/jobs", status_code=201)
    def start_job(req: JobRequest) -> dict[str, str]:
        if active():
            raise HTTPException(409, "A job is already running. Wait for it or cancel it first.")
        job = Job(id=uuid.uuid4().hex[:12], options=JobOptions(**req.model_dump()))
        jobs[job.id] = job
        state["active"] = job
        pipeline = Pipeline(client, library, emit=job.push, cancel=job.cancel)
        threading.Thread(target=pipeline.run, args=(job.options,), daemon=True, name=f"job-{job.id}").start()
        return {"id": job.id}

    def get_job(job_id: str) -> Job:
        if job_id not in jobs:
            raise HTTPException(404, "Job not found")
        return jobs[job_id]

    @app.post("/api/jobs/{job_id}/cancel")
    def cancel_job(job_id: str) -> dict[str, bool]:
        get_job(job_id).cancel.set()
        return {"ok": True}

    @app.get("/api/jobs/{job_id}/events")
    async def job_events(job_id: str) -> StreamingResponse:
        job = get_job(job_id)

        async def stream():
            seq = 0
            while True:
                for event in job.since(seq):
                    seq = event["seq"] + 1
                    yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
                if job.done and seq >= len(job.events):
                    return
                yield ": keep-alive\n\n"
                await asyncio.sleep(0.4)

        return StreamingResponse(
            stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"}
        )

    @app.get("/api/library")
    def list_library(q: str = "") -> dict[str, Any]:
        items = library.entries(q)
        return {"items": items, "count": len(items)}

    @app.get("/api/library/{uid}")
    def read_transcript(uid: str) -> dict[str, Any]:
        entry = library.get(uid)
        transcript = library.load_transcript(uid)
        if not entry or not transcript:
            raise HTTPException(404, "Transcript not found")
        data = json.loads(exporters.to_json(transcript))
        return {
            "entry": entry,
            "rtl": is_rtl(transcript.language),
            "paragraphs": data["paragraphs"],
            "text": data["text"],
        }

    @app.get("/api/library/{uid}/download")
    def download(uid: str, format: str = "md", timestamps: bool = False) -> Response:
        transcript = library.load_transcript(uid)
        if not transcript or format not in exporters.FORMATS:
            raise HTTPException(404, "Not found")
        ext, mime, _ = exporters.FORMATS[format]
        body = exporters.render(transcript, format, timestamps=timestamps)
        name = f"{safe_filename(transcript.video.title)}.{transcript.language}.{ext}"
        return Response(
            body,
            media_type=f"{mime}; charset=utf-8",
            headers={"Content-Disposition": _attachment(name)},
        )

    @app.get("/api/export.zip")
    def export_zip(format: str = "md", q: str = "", timestamps: bool = False) -> Response:
        if format not in exporters.FORMATS:
            raise HTTPException(400, "Unknown format")
        ext = exporters.FORMATS[format][0]
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
            for entry in library.entries(q):
                t = library.load_transcript(entry["uid"])
                if t:
                    path = f"{safe_filename(t.collection, 80)}/{safe_filename(t.video.title)} [{t.video.id}].{t.language}.{ext}"
                    zf.writestr(path, exporters.render(t, format, timestamps=timestamps))
        return Response(
            buffer.getvalue(),
            media_type="application/zip",
            headers={"Content-Disposition": _attachment(f"tubescribe-{format}.zip")},
        )

    @app.post("/api/open-folder")
    def open_folder() -> dict[str, bool]:
        library.root.mkdir(parents=True, exist_ok=True)
        _reveal(library.root)
        return {"ok": True}

    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(WEB_DIR / "index.html")

    app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")
    return app


def _attachment(filename: str) -> str:
    from urllib.parse import quote

    ascii_name = filename.encode("ascii", "ignore").decode() or "transcript"
    return f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(filename)}"


def _reveal(path: Path) -> None:
    if sys.platform == "win32":
        os.startfile(path)
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(path)])
    else:
        subprocess.Popen(["xdg-open", str(path)])
