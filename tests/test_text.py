from tubescribe import text as T
from tubescribe.models import Segment


def segs(*texts, step=1.0):
    return [Segment(i * step, i * step + step, t) for i, t in enumerate(texts)]


def test_clean_removes_annotations_in_any_language():
    out = T.clean_segments(segs("[Music]", "Hello [Applause] world", "[音楽]", "♪ la la ♪"))
    assert [s.text for s in out] == ["Hello world", "la la"]


def test_join_handles_cjk_without_spaces():
    assert T.join(["今日は", "いい天気です。"], "ja") == "今日はいい天気です。"
    assert T.join(["Hello", ",", "world"], "en") == "Hello, world"


def test_paragraphs_break_on_sentences_after_target():
    words = [f"word{i}." if i % 10 == 9 else f"word{i}" for i in range(60)]
    paras = T.paragraphs(segs(*words), "en", target_words=20)
    assert len(paras) == 3
    assert all(p.text.endswith(".") for p in paras)
    assert paras[1].start == 20.0


def test_paragraphs_force_break_without_punctuation():
    paras = T.paragraphs(segs(*["word"] * 100), "en", target_words=20)
    assert len(paras) >= 2


def test_paragraphs_break_on_long_pause():
    s = [Segment(0, 1, "one two three four five six seven eight nine ten."),
         Segment(10, 11, "after a long pause")]
    assert len(T.paragraphs(s, "en", target_words=30)) == 2


def test_arabic_sentence_end_is_recognised():
    words = ["كلمة"] * 19 + ["نهاية؟"] + ["أخرى"] * 5
    paras = T.paragraphs(segs(*words), "ar", target_words=20)
    assert paras[0].text.endswith("؟")


def test_counts_and_timestamps():
    assert T.word_count("a b c", "en") == 3
    assert T.word_count("今日は 天気", "ja") == 5
    assert T.timestamp(3725) == "1:02:05"
    assert T.timestamp(65) == "01:05"
    assert T.srt_timestamp(3.5) == "00:00:03,500"
