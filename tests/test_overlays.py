import pytest

from iodc import overlays
from iodc.views import CLOSE, WIDE


def test_every_view_language_and_time_of_day_has_an_overlay():
    for view in (WIDE, CLOSE):
        for lang in overlays.languages():
            for night in (False, True):
                assert overlays.find(view.key, lang, night)


def test_both_languages_are_present():
    """The app switches map labels with its language setting, so a missing
    language would strand half the users on the other one."""
    assert set(overlays.languages()) == {"bn", "en"}


def test_overlays_load_at_exactly_the_frame_size():
    for view in (WIDE, CLOSE):
        image = overlays.load(view, "bn", night=False)
        assert image.size == view.size
        assert image.mode == "RGBA"


def test_an_overlay_drawn_for_another_rectangle_is_refused():
    """The dangerous case: right pixel size, wrong geography. It would
    composite perfectly and produce a confident, wrong map."""
    from dataclasses import replace
    shifted = replace(WIDE, bbox=replace(WIDE.bbox, min_lat=WIDE.bbox.min_lat + 1))
    with pytest.raises(overlays.OverlayMismatch, match="wrong map"):
        overlays.load(shifted, "bn", night=False)


def test_a_missing_combination_names_what_was_asked_for():
    with pytest.raises(FileNotFoundError, match="lang=fr"):
        overlays.find("wide", "fr", night=False)
