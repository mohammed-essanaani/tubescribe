from tubescribe import parsers

from .conftest import JSON3_SAMPLE, ROLLUP_VTT


def test_json3_joins_word_segments_and_skips_newline_events():
    segs = parsers.parse_json3(JSON3_SAMPLE)
    assert [s.text for s in segs] == ["Why do you get a brain freeze?", "That sudden & intense pain."]
    assert segs[0].start == 0.16
    assert round(segs[1].end, 2) == 5.92


def test_vtt_removes_rollup_duplicates_and_inline_tags():
    segs = parsers.parse_vtt(ROLLUP_VTT)
    text = " ".join(s.text for s in segs)
    assert text == "Why do you get a brain freeze? [Music]"
    assert "<c>" not in text


def test_vtt_handles_bytes_and_hour_timestamps():
    vtt = b"WEBVTT\n\n01:02:03.500 --> 01:02:05.000\nHello\n"
    (seg,) = parsers.parse_vtt(vtt)
    assert seg.start == 3723.5 and seg.text == "Hello"


def test_parse_dispatches_by_extension():
    assert parsers.parse(JSON3_SAMPLE, "json3")
    try:
        parsers.parse("x", "srv9")
    except ValueError:
        pass
    else:
        raise AssertionError("expected ValueError")
