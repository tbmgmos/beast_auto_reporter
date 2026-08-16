from src.me_tracks import (
    detect_me_track_variant,
    infer_me_track_variant,
    iter_dynamic_me_pairs,
    me_assignment_label,
    me_track_key,
    parse_me_assignment_label,
)


def test_detects_dx_and_lettered_opt_variants():
    assert detect_me_track_variant("episode_20_DX.wav") == ("dx", "")
    assert detect_me_track_variant("episode_51_OPT_A.wav") == ("opt", "a")
    assert detect_me_track_variant("episode_OPT_B_20.pdf") == ("opt", "b")


def test_recognizes_industry_me_dx_and_optional_aliases():
    assert infer_me_track_variant("show_20_M+E.wav") == ("me", "", "strong")
    assert infer_me_track_variant("show_51_International Sound.wav") == ("me", "", "strong")
    assert infer_me_track_variant("show_20_Footsteps.wav") == ("me", "", "strong")
    assert infer_me_track_variant("show_51_Dialogue Guide.wav") == ("dx", "", "strong")
    assert infer_me_track_variant("show_20_REMOVE.wav") == ("dx", "", "strong")
    assert infer_me_track_variant("show_51_OPTB.wav") == ("opt", "b", "strong")
    assert infer_me_track_variant("show_20_Optional_4.wav") == ("opt", "4", "strong")
    assert infer_me_track_variant("show_20_Optionals_A.wav") == ("opt", "a", "strong")


def test_optional_content_names_are_reviewable_heuristics():
    assert infer_me_track_variant("show_20_Efforts.wav") == ("opt", "a", "heuristic")
    assert infer_me_track_variant("show_51_Group_ADR.wav") == ("opt", "b", "heuristic")
    assert infer_me_track_variant("show_20_Foreign_Dialogue.wav") == ("opt", "c", "heuristic")
    assert infer_me_track_variant("show_51_Archival.wav") == ("opt", "d", "heuristic")
    assert infer_me_track_variant("show_20_Song_Vocals.wav") == ("opt", "e", "heuristic")


def test_unknown_internal_filename_requires_manual_assignment():
    assert infer_me_track_variant("A003_017_0825XZ.wav") == (None, "", "unknown")


def test_manual_assignment_labels_support_custom_opt_letters_and_numbers():
    assert parse_me_assignment_label("2.0 DX", "audio") == "audio_me_dx_20"
    assert parse_me_assignment_label("5.1 OPT F", "pdf") == "pdf_me_opt_f_51"
    assert parse_me_assignment_label("2.0 OPT 12", "audio") == "audio_me_opt_12_20"
    assert parse_me_assignment_label("2.0 OPT", "audio") == "audio_me_opt_20"
    assert me_assignment_label("audio_me_opt_12_20") == "2.0 OPT 12"


def test_builds_distinct_keys_for_more_than_three_opt_variants():
    keys = {
        me_track_key("opt", "20", variant=variant)
        for variant in ("a", "b", "c", "d")
    }
    assert len(keys) == 4


def test_dynamic_pairs_are_ordered_dx_then_all_opt_variants():
    tech_info = {
        "audio_me_opt_c_51": {},
        "audio_me_dx_20": {},
        "pdf_me_opt_a_20": {},
        "audio_me_opt_c_20": {},
        "audio_me_dx_51": {},
        "audio_me_opt_a_51": {},
    }
    assert [label for label, _audio, _pdf in iter_dynamic_me_pairs(tech_info)] == [
        "20 DX", "51 DX", "20 OPT A", "51 OPT A", "20 OPT C", "51 OPT C",
    ]
