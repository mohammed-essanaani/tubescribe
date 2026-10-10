"""Command-line entry point. Running `tubescribe` with no command starts the web UI."""

from __future__ import annotations

import argparse
import os
import sys
import threading
import webbrowser
from pathlib import Path
from typing import Any

from . import __version__, exporters
from .languages import ORIGINAL
from .pipeline import JobOptions, Pipeline
from .storage import Library
from .throttle import DEFAULT_PROFILE, PROFILES
from .youtube import ClientOptions, YouTubeClient, YouTubeError

DEFAULT_PORT = 8765


def default_output() -> Path:
    if env := os.environ.get("TUBESCRIBE_OUTPUT"):
        return Path(env)
    if getattr(sys, "frozen", False):  # running as a PyInstaller exe
        return Path(sys.executable).parent / "transcripts"
    return Path.cwd() / "transcripts"


def _supports_color() -> bool:
    if os.environ.get("NO_COLOR") or not sys.stdout.isatty():
        return False
    if sys.platform == "win32":
        os.system("")  # enable ANSI colors on Windows
    return True


_COLOR = _supports_color()


def _c(text: str, code: str) -> str:
    return f"\033[{code}m{text}\033[0m" if _COLOR else text


def _print_event(event: dict[str, Any]) -> None:
    kind = event["type"]
    prefix = f"[{event['index']}/{event['total']}] " if "index" in event else ""
    title = (event.get("title") or "")[:70]
    if kind == "status":
        print(_c(event["message"], "36"))
    elif kind == "resolved":
        print(_c(f"Found {event['total']} video(s) in {event['kind']}: {event['title']}", "32"))
    elif kind == "video_done":
        e = event["entry"]
        details = f"{e['language']} · {e['kind']} · {e['words']} words"
        print(f"{prefix}{_c('✓', '32')} {title}  {_c(details, '90')}")
    elif kind == "video_skipped":
        print(f"{prefix}{_c('↷', '90')} {title}  {_c(event['reason'], '90')}")
    elif kind == "video_no_captions":
        print(f"{prefix}{_c('○', '33')} {title}  {_c(event['reason'], '33')}")
    elif kind == "video_failed":
        print(f"{prefix}{_c('✗', '31')} {title}  {_c(event['reason'], '31')}")
    elif kind == "waiting" and event["seconds"] >= 10:
        print(_c(f"   {event['reason']} ({event['seconds']:.0f}s)", "90"))
    elif kind == "error":
        print(_c(f"\n{event['message']}", "31"))
    elif kind == "finished":
        s = event["summary"]
        print(
            "\n"
            + _c("Done. ", "1")
            + f"{s['saved']} saved, {s['skipped']} already had, "
            f"{s['no_captions']} without captions, {s['failed']} failed."
        )


def _add_network_args(p: argparse.ArgumentParser) -> None:
    g = p.add_argument_group("network (optional)")
    g.add_argument("--cookies", metavar="FILE", help="Netscape cookies.txt file")
    g.add_argument(
        "--cookies-from-browser", metavar="BROWSER",
        help="Reuse your browser session, e.g. firefox, chrome, edge, 'chrome:Profile 1'",
    )
    g.add_argument("--proxy", metavar="URL", help="HTTP/SOCKS proxy, e.g. socks5://127.0.0.1:1080")


def _client_options(args: argparse.Namespace) -> ClientOptions:
    return ClientOptions(
        cookies_file=args.cookies,
        cookies_from_browser=args.cookies_from_browser,
        proxy=args.proxy,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tubescribe",
        description="Polite, multilingual YouTube transcript extractor.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = parser.add_subparsers(dest="command")

    get = sub.add_parser("get", help="Extract transcripts for a video, playlist or channel")
    get.add_argument("url", help="Video / Shorts / playlist / channel URL, @handle or video ID")
    get.add_argument(
        "-l", "--lang", default=ORIGINAL,
        help="Preferred languages, comma separated, in order. "
        f"'{ORIGINAL}' = the video's spoken language (default). Example: en,fr",
    )
    get.add_argument(
        "--no-translate", action="store_true",
        help="Never use YouTube's machine translation; fall back to the original language",
    )
    get.add_argument("--manual-only", action="store_true", help="Skip auto-generated captions")
    get.add_argument(
        "-f", "--format", default="md",
        help=f"Output formats, comma separated: {', '.join(exporters.FORMATS)} (default: md)",
    )
    get.add_argument("-t", "--timestamps", action="store_true", help="Add paragraph timestamps")
    get.add_argument("-n", "--limit", type=int, help="Only the N most recent videos")
    get.add_argument(
        "-s", "--speed", choices=list(PROFILES), default=DEFAULT_PROFILE,
        help="Request pacing (default: balanced)",
    )
    get.add_argument("--force", action="store_true", help="Re-download videos already in the library")
    get.add_argument("-o", "--output", type=Path, default=None, help="Output folder")
    _add_network_args(get)

    langs = sub.add_parser("langs", help="List caption languages available for a video")
    langs.add_argument("url")
    _add_network_args(langs)

    ui = sub.add_parser("ui", help="Launch the web interface (default)")
    ui.add_argument("--host", default="127.0.0.1")
    ui.add_argument("--port", type=int, default=DEFAULT_PORT)
    ui.add_argument("--no-browser", action="store_true", help="Don't open a browser tab")
    ui.add_argument("-o", "--output", type=Path, default=None, help="Output folder")
    _add_network_args(ui)
    return parser


def cmd_get(args: argparse.Namespace) -> int:
    formats = [f.strip() for f in args.format.split(",") if f.strip()]
    unknown = [f for f in formats if f not in exporters.FORMATS]
    if unknown:
        print(f"Unknown format(s): {', '.join(unknown)}", file=sys.stderr)
        return 2

    output = args.output or default_output()
    opts = JobOptions(
        url=args.url,
        languages=[x.strip() for x in args.lang.split(",") if x.strip()] or [ORIGINAL],
        allow_translation=not args.no_translate,
        allow_auto=not args.manual_only,
        formats=formats,
        timestamps=args.timestamps,
        limit=args.limit,
        speed=args.speed,
        force=args.force,
    )
    cancel = threading.Event()
    pipeline = Pipeline(YouTubeClient(_client_options(args)), Library(output), emit=_print_event, cancel=cancel)
    print(_c(f"TubeScribe {__version__}", "1") + _c(f"  {Path(output).resolve()}", "90"))
    try:
        summary = pipeline.run(opts)
    except KeyboardInterrupt:
        cancel.set()
        print("\nCancelled.")
        return 130
    return 1 if summary.stopped_reason or (summary.failed and not summary.saved) else 0


def cmd_langs(args: argparse.Namespace) -> int:
    client = YouTubeClient(_client_options(args))
    try:
        info = client.video_info(args.url)
    except YouTubeError as exc:
        print(_c(str(exc), "31"), file=sys.stderr)
        return 1
    tracks = client.tracks(info)
    meta = client.meta(info, tracks)
    print(_c(meta.title, "1"))
    print(f"Spoken language: {meta.original_language or 'unknown'}\n")
    shown = [t for t in tracks if t.kind != "translated"]
    if not shown:
        print("No captions available.")
    for t in shown:
        print(f"  {t.lang:<10} {t.kind:<7} {t.name}")
    translated = sum(t.kind == "translated" for t in tracks)
    if translated:
        print(_c(f"\n  + {translated} machine-translated languages available (use --lang CODE)", "90"))
    return 0


def cmd_ui(args: argparse.Namespace) -> int:
    import uvicorn

    from .server import create_app

    output = args.output or default_output()
    app = create_app(output, _client_options(args))
    url = f"http://{'localhost' if args.host in ('127.0.0.1', '0.0.0.0') else args.host}:{args.port}"
    print(_c(f"TubeScribe {__version__}", "1") + f" running at {_c(url, '36')}")
    print(_c(f"Transcripts folder: {Path(output).resolve()}", "90"))
    print(_c("Close this window or press Ctrl+C to quit.", "90"))
    if not args.no_browser:
        threading.Timer(1.2, webbrowser.open, args=(url,)).start()
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")
    return 0


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if stream and hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    parser = build_parser()
    argv = sys.argv[1:] if argv is None else argv
    args = parser.parse_args(argv or ["ui"])
    if args.command == "get":
        return cmd_get(args)
    if args.command == "langs":
        return cmd_langs(args)
    return cmd_ui(args)
