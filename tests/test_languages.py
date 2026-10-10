from tubescribe.languages import base, is_rtl, list_tracks, normalize, original_language, pick_track

from .conftest import make_info


def test_normalize_and_base():
    assert normalize("en-orig") == "en"
    assert normalize("pt_BR") == "pt-BR"
    assert normalize("EN") == "en"
    assert base("zh-Hans") == "zh"
    assert is_rtl("ar") and is_rtl("he") and not is_rtl("fr")


def test_list_tracks_classifies_kinds_and_skips_live_chat():
    tracks = list_tracks(make_info(manual=["es"], auto_orig="ar"))
    kinds = {(t.key, t.kind) for t in tracks}
    assert ("es", "manual") in kinds
    assert ("ar-orig", "auto") in kinds
    assert ("fr", "translated") in kinds
    assert not any(t.key == "live_chat" for t in tracks)


def test_original_language_prefers_asr_track():
    info = make_info(manual=["en"], auto_orig="ar", language="en")
    assert original_language(info, list_tracks(info)) == "ar"


def test_default_picks_original_language_manual_over_auto():
    info = make_info(manual=["ar"], auto_orig="ar")
    tracks = list_tracks(info)
    t = pick_track(tracks, original="ar")
    assert (t.lang, t.kind) == ("ar", "manual")


def test_original_falls_back_to_asr():
    tracks = list_tracks(make_info(auto_orig="ja"))
    t = pick_track(tracks, original="ja")
    assert (t.key, t.kind) == ("ja-orig", "auto")


def test_preferred_language_uses_translation_when_allowed():
    tracks = list_tracks(make_info(auto_orig="en"))
    assert pick_track(tracks, ["fr"], original="en").kind == "translated"
    no_tr = pick_track(tracks, ["fr"], original="en", allow_translation=False)
    assert (no_tr.lang, no_tr.kind) == ("en", "auto")


def test_regional_variant_matches_base_language():
    tracks = list_tracks(make_info(manual=["pt-BR"]))
    assert pick_track(tracks, ["pt"]).key == "pt-BR"


def test_manual_only_mode_ignores_asr():
    tracks = list_tracks(make_info(auto_orig="en"))
    assert pick_track(tracks, original="en", allow_auto=False) is None


def test_no_tracks():
    assert pick_track([], ["en"]) is None
