import json

from tubescribe import exporters
from tubescribe.models import CaptionTrack, Segment, Transcript, VideoMeta
from tubescribe.storage import Library, request_signature, safe_filename


def transcript(lang="en", title="Hello: World?"):
    return Transcript(
        video=VideoMeta(id="abcdefghijk", title=title, url="https://www.youtube.com/watch?v=abcdefghijk",
                        channel="Chan", upload_date="2025-01-02", duration=125),
        track=CaptionTrack("en-orig", lang, "English (Original)", "auto"),
        segments=[Segment(0, 2, "First sentence."), Segment(65, 67, "Second one.")],
        collection="My / Playlist",
        fetched_at="2026-01-01T00:00:00+00:00",
    )


def test_safe_filename_keeps_unicode_and_strips_illegal():
    assert safe_filename('a<b>:"c"/d') == "a b c d"
    assert safe_filename("مرحبا بالعالم") == "مرحبا بالعالم"
    assert safe_filename("CON") == "_CON"
    assert safe_filename("...") == "untitled"
    assert len(safe_filename("x " * 200)) <= 100


def test_signature_depends_on_request():
    assert request_signature(["orig"], True, True) != request_signature(["fr"], True, True)


def test_markdown_has_front_matter_and_timestamp_links():
    md = exporters.to_markdown(transcript(), timestamps=True)
    assert md.startswith("---\ntitle: \"Hello: World?\"")
    assert 'captions: "auto"' in md
    assert "(https://youtu.be/abcdefghijk?t=0)" in md


def test_srt_and_json():
    srt = exporters.to_srt(transcript())
    assert "1\n00:00:00,000 --> 00:00:02,000\nFirst sentence." in srt
    data = json.loads(exporters.to_json(transcript()))
    assert data["stats"]["words"] == 4
    assert data["paragraphs"][0]["text"].startswith("First sentence.")


def test_library_save_index_and_reload(tmp_path):
    lib = Library(tmp_path)
    entry = lib.save(transcript(), signature="deadbeef", formats=["md", "txt"])
    assert lib.has("abcdefghijk", "deadbeef")
    assert not lib.has("abcdefghijk", "00000000")
    assert (tmp_path / entry["files"]["md"]).read_text(encoding="utf-8").startswith("---")
    assert entry["files"]["md"].startswith("My Playlist/")

    reopened = Library(tmp_path)
    assert reopened.entries("hello")[0]["uid"] == entry["uid"]
    loaded = reopened.load_transcript(entry["uid"])
    assert loaded.segments[1].text == "Second one."


def test_library_refuses_path_traversal(tmp_path):
    lib = Library(tmp_path / "lib")
    (tmp_path / "secret.txt").write_text("x")
    assert lib.resolve_file("../secret.txt") is None
    assert lib.load_transcript("../../etc") is None
