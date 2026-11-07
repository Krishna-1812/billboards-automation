"""The creative checks on real files: what is measured, and how Claude's reading becomes checks."""

import os
from types import SimpleNamespace

import pytest

from billboard_ops import creative_checks as cc

from media import H, W, media  # noqa: F401  (pytest fixture)

SPEC = SimpleNamespace(width_px=W, height_px=H, file_types="mp4,jpg,png", video_codecs="h264", durations_s="10",
                       duration_tolerance_s=0.2, fps_min=25, fps_max=30, max_file_mb=50, audio_allowed=False,
                       safe_margin_pct=5)


def run(path, secs=10, spec=SPEC):
    info = cc.probe(path, os.path.basename(path))
    scan = cc.scan_video(path, info.get("duration")) if info["kind"] == "video" else None
    return info, {c["key"]: c for c in cc.technical(info, scan, spec, secs)}


def failing(checks):
    return sorted(k for k, c in checks.items() if c["status"] == "fail")


def test_a_good_video_passes_everything(media):
    info, checks = run(media["good.mp4"])
    assert (info["kind"], info["width"], info["height"], info["fps"]) == ("video", W, H, 25.0)
    assert info["duration"] == pytest.approx(10, abs=0.05)
    assert failing(checks) == [] and all(c["status"] in ("pass", "skip") for c in checks.values())


def test_each_fault_is_found_and_only_that_one(media):
    assert failing(run(media["long.mp4"])[1]) == ["duration"]
    _, c = run(media["long.mp4"])
    assert c["duration"]["measured"] == "15.0s" and c["duration"]["required"] == "10s"
    _, c = run(media["portrait.mp4"])
    assert failing(c) == ["resolution"] and "portrait file for a landscape screen" in c["resolution"]["detail"]
    assert failing(run(media["sound.mp4"])[1]) == ["sound"]
    _, c = run(media["blackstart.mp4"])
    assert failing(c) == ["black_frames"] and c["black_frames"]["measured"] == "2.0s black at the start"
    _, c = run(media["letterbox.mp4"])
    assert failing(c) == ["borders"] and "640x260" in c["borders"]["measured"]


def test_a_15s_slot_takes_the_15s_file(media):
    assert failing(run(media["long.mp4"], secs=15)[1]) == []


def test_images(media):
    info, c = run(media["good.png"])
    assert info["kind"] == "image" and failing(c) == [] and c["duration"]["status"] == "skip"
    _, c = run(media["cmyk.jpg"])
    assert failing(c) == ["colour"]
    png_only = SimpleNamespace(**{**SPEC.__dict__, "file_types": "mp4"})
    assert failing(run(media["good.png"], spec=png_only)[1]) == ["file_type"]


def test_damaged_and_unreadable_files(media):
    _, c = run(media["broken.mp4"])
    assert "damaged" in failing(c)
    info, c = run(media["notes.txt"])
    assert info["readable"] is False and failing(c) == ["damaged"]


def test_stills_are_taken_at_every_scene_and_the_end(media, tmp_path):
    info = cc.probe(media["blackstart.mp4"], "blackstart.mp4")
    frames = cc.sample_frames(media["blackstart.mp4"], info, str(tmp_path / "f"))
    times = [t for t, _ in frames]
    assert times[0] == 0 and 2.0 in times and times[-1] >= 9.0 and all(j[:2] == b"\xff\xd8" for _, j in frames)
    info = cc.probe(media["good.png"], "good.png")
    assert len(cc.sample_frames(media["good.png"], info, str(tmp_path / "g"))) == 1


# ── Claude's reading, turned into checks ────────────────────────────────────────

FRAMES = [(0.0, b""), (2.0, b""), (4.0, b""), (9.7, b"")]


def answer(**kw):
    base = {"texts": [{"frame": 1, "text": "عرض خاص", "language": "arabic", "other_language": None,
                       "placement": "inside", "size": "large"},
                      {"frame": 4, "text": "Special offer", "language": "english", "other_language": None,
                       "placement": "inside", "size": "normal"}],
            "language_verdict": "arabic_and_english", "other_languages": [], "arabic_rendering": "ok",
            "arabic_issue": "", "arabic_issue_frames": [], "spelling": [], "brand_seen": "Acme",
            "brand_match": "yes", "readability": [], "rule_breaches": [], "notes": ""}
    base.update(kw)
    return base


def content(raw, rules=("No alcohol",)):
    return {c["key"]: c for c in cc.content_checks(raw, FRAMES, list(rules))}


def test_a_clean_bilingual_ad_passes():
    c = content(answer())
    assert all(x["status"] == "pass" for x in c.values()) and c["language"]["measured"] == "Arabic and English"


def test_the_language_rule():
    eng = [{"frame": 2, "text": "Big sale", "language": "english", "other_language": None, "placement": "inside",
            "size": "large"}]
    c = content(answer(texts=eng, language_verdict="english_only"))
    assert c["language"]["status"] == "fail" and c["language"]["at"] == 2.0 and c["language"]["frames"] == [2]
    hindi = answer(texts=answer()["texts"] + [{"frame": 3, "text": "सेल", "language": "other",
                                               "other_language": "Hindi", "placement": "inside", "size": "large"}])
    c = content(hindi)            # Claude said Arabic and English, but saw Hindi text: that fails
    assert c["language"]["status"] == "fail" and c["language"]["measured"] == "Hindi"
    c = content(answer(texts=[], language_verdict="no_text", arabic_rendering="no_arabic"))
    assert c["language"]["status"] == "pass" and c["arabic_rendering"]["status"] == "skip"


def test_broken_arabic_spelling_brand_edges_and_rules():
    c = content(answer(arabic_rendering="broken", arabic_issue="letters unjoined", arabic_issue_frames=[1],
                       spelling=[{"frame": 4, "found": "Speical", "should_be": "Special"}], brand_match="no",
                       brand_seen="Other Co", rule_breaches=[{"rule": "No alcohol", "frame": 3, "detail": "a beer"}]))
    assert {k for k, x in c.items() if x["status"] == "fail"} == {"arabic_rendering", "spelling", "brand",
                                                                  "content_rules"}
    assert c["spelling"]["detail"] == "“Speical” → “Special”" and c["content_rules"]["at"] == 4.0
    cut = answer(texts=[dict(answer()["texts"][0], placement="cut_off"), dict(answer()["texts"][1], size="too_small")])
    c = content(cut)
    assert c["safe_area"]["status"] == "fail" and c["readability"]["status"] == "warn"
    assert content(answer(brand_match="unclear"))["brand"]["status"] == "warn"
    assert content(answer(), rules=())["content_rules"]["status"] == "skip"


def test_verdict():
    assert cc.verdict([{"status": "pass"}, {"status": "skip"}]) == "passed"
    assert cc.verdict([{"status": "pass"}, {"status": "warn"}]) == "warnings"
    assert cc.verdict([{"status": "warn"}, {"status": "fail"}]) == "failed"
