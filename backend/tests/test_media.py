import json
import shutil
import subprocess
from fractions import Fraction
from pathlib import Path

import av
import pytest
from app.config import Settings
from app.media import (
    MediaError,
    _validate_native_duration,
    cut_clip,
    generate_demo,
    prepare_media,
    prepare_video_input,
    probe_video,
    sample_frames,
)

pytestmark = pytest.mark.skipif(
    not shutil.which("ffmpeg") or not shutil.which("ffprobe"), reason="FFmpeg tools required"
)


def make_vfr(path):
    """Actual nonuniform display timestamps, with a nonzero source origin."""
    with av.open(str(path), "w") as container:
        stream = container.add_stream("libx264", rate=10)
        stream.width, stream.height, stream.pix_fmt = 64, 48, "yuv420p"
        stream.time_base = Fraction(1, 1000)
        stream.codec_context.time_base = Fraction(1, 1000)
        stream.options = {"bf": "0", "crf": "10"}
        for i, pts in enumerate([5000, 5100, 5250, 5500, 5700, 5900]):
            frame = av.VideoFrame(64, 48, "rgb24")
            frame.planes[0].update(bytes((30 + i * 30, 50, 70)) * 64 * 48)
            frame.pts, frame.time_base = pts, Fraction(1, 1000)
            for packet in stream.encode(frame):
                container.mux(packet)
        for packet in stream.encode(None):
            container.mux(packet)


@pytest.fixture
def prepared(tmp_path):
    settings = Settings(data_dir=tmp_path)
    settings.ensure_dirs()
    source = tmp_path / "media" / "vfr.mp4"
    make_vfr(source)
    media = {"id": "vfr_test", "original_path": "media/vfr.mp4"}
    media.update(prepare_media(media, settings))
    return media, settings


def test_vfr_index_keeps_original_pts_and_zeroes_first_display_frame(prepared):
    media, settings = prepared
    index = json.loads((settings.data_dir / media["frame_index_path"]).read_text())
    assert [f["source_time_us"] for f in index["frames"]] == [
        0, 100000, 250000, 500000, 700000, 900000,
    ]
    assert index["source_origin_pts"] * Fraction(index["source_time_base"]) == 5
    assert media["duration_us"] == 1000000
    assert media["frame_count"] == 6
    mapping = json.loads((settings.data_dir / media["preview_mapping_path"]).read_text())
    assert [f["source_time_us"] for f in mapping["frames"]] == [
        0, 100000, 250000, 500000, 700000, 900000,
    ]
    assert [f["preview_time_us"] for f in mapping["frames"]] == [
        0, 100000, 250000, 500000, 700000, 900000,
    ]


def test_sampling_uses_real_vfr_frames_and_actual_input_zero(prepared):
    media, settings = prepared
    result = sample_frames(media, 150000, 800000, 4, 8, settings.data_dir / "sample", settings)
    assert result["input_origin_us"] == 250000
    assert [f["source_time_us"] for f in result["frames"]] == [250000, 500000, 700000]
    assert [f["local_time_s"] for f in result["frames"]] == [0, 0.25, 0.45]
    assert [f["frame_index"] for f in result["frames"]] == [2, 3, 4]
    assert result["source_range_us"] == [150000, 800000]
    assert result["max_gap_us"] == 250000
    # JPEG output is a real decodable image and comes from each selected source frame.
    for item in result["frames"]:
        with av.open(item["path"]) as image:
            frame = next(image.decode(video=0))
            assert (frame.width, frame.height) == (64, 48)


def test_sampling_limit_is_an_error_not_silent_downsampling(prepared):
    media, settings = prepared
    with pytest.raises(MediaError) as caught:
        sample_frames(media, 0, 1000000, 5, 4, settings.data_dir / "too_many", settings)
    assert caught.value.code == "sampling_limit_exceeded"
    assert not (settings.data_dir / "too_many").exists()


def test_sampling_never_duplicates_frames_and_end_is_exclusive(prepared):
    media, settings = prepared
    sampled = sample_frames(media, 0, 500000, 100, 6, settings.data_dir / "dense", settings)
    assert [f["source_time_us"] for f in sampled["frames"]] == [0, 100000, 250000]
    assert len({f["frame_id"] for f in sampled["frames"]}) == 3


def test_clip_selects_actual_source_frames_not_requested_offset(prepared):
    media, settings = prepared
    output = settings.data_dir / "clips" / "test.mp4"
    result = cut_clip(media, 150000, 700000, output, settings)
    assert result["requested_range_us"] == [150000, 700000]
    assert result["actual_range_us"] == [250000, 700000]
    assert result["source_first_frame"]["frame_index"] == 2
    assert result["source_last_frame"]["frame_index"] == 3
    with av.open(str(output)) as container:
        frames = list(container.decode(video=0))
    assert len(frames) == 2
    assert (frames[0].width, frames[0].height) == (64, 48)
    first_pts = frames[0].pts * frames[0].time_base
    assert [round((f.pts * f.time_base - first_pts) * 1000000) for f in frames] == [0, 250000]
    # The selected source frame is visibly different from its preceding frame.
    pixels = bytes(frames[0].to_rgb().planes[0])
    assert abs(pixels[0] - 90) < 12


def test_cancel_and_path_escape_are_explicit(prepared):
    media, settings = prepared
    with pytest.raises(MediaError) as caught:
        sample_frames(media, 0, 1000000, 1, 5, settings.data_dir / "cancel", settings,
                      cancelled=lambda: True)
    assert caught.value.code == "cancelled"
    with pytest.raises(MediaError) as caught:
        cut_clip(media, 0, 500000, settings.data_dir / "../escape.mp4", settings)
    assert caught.value.code == "invalid_media_path"


def test_demo_is_labeled_fixture_with_real_twelve_second_video(tmp_path):
    output = tmp_path / "demo.mp4"
    info = generate_demo(output)
    assert info["is_demo"] is True
    assert "not AI accuracy" in info["fixture_description"]
    assert info["duration_us"] == 12000000
    assert len(info["fixture_events"]) == 4
    assert {e["kind"] for e in info["fixture_events"]} == {"point", "interval"}
    assert probe_video(output)["width"] == 320


def test_phone_rotation_is_consistent_in_preview_samples_and_clips(tmp_path):
    settings = Settings(data_dir=tmp_path)
    settings.ensure_dirs()
    original = tmp_path / "media" / "unrotated.mp4"
    make_vfr(original)
    rotated = tmp_path / "media" / "rotated.mp4"
    capabilities = subprocess.run(["ffmpeg", "-h", "full"], capture_output=True, text=True,
                                  check=True).stdout
    rotation_input = ["-display_rotation", "90"] if "-display_rotation" in capabilities else []
    rotation_output = [] if rotation_input else ["-metadata:s:v:0", "rotate=90"]
    subprocess.run([
        "ffmpeg", "-nostdin", "-v", "error", "-y", *rotation_input,
        "-i", str(original), "-c", "copy", *rotation_output, str(rotated),
    ], check=True)
    media = {"id": "rotated", "original_path": "media/rotated.mp4"}
    media.update(prepare_media(media, settings))
    assert media["rotation"] == 90
    with av.open(str(tmp_path / media["preview_path"])) as container:
        preview_frame = next(container.decode(video=0))
    sampled = sample_frames(media, 0, 200000, 10, 4, tmp_path / "samples", settings)
    with av.open(sampled["frames"][0]["path"]) as container:
        sample_frame = next(container.decode(video=0))
    clip = tmp_path / "clip.mp4"
    cut_clip(media, 0, 250000, clip, settings)
    with av.open(str(clip)) as container:
        clip_frame = next(container.decode(video=0))
    assert {(f.width, f.height) for f in (preview_frame, sample_frame, clip_frame)} == {(48, 64)}


def test_native_video_window_has_real_local_zero_vfr_mapping_and_no_jpegs(prepared):
    media, settings = prepared
    directory = settings.data_dir / "native"
    manifest = prepare_video_input(media, 150000, 700000, 4, 8, directory, settings, max_width=32)
    assert manifest["input_mode"] == "video"
    assert manifest["input_origin_us"] == 250000
    assert manifest["requested_range_us"] == [150000, 700000]
    assert manifest["source_range_us"] == [250000, 700000]
    assert manifest["actual_sampling_known"] is False
    assert manifest["sample_fps"] == 4
    assert manifest["nominal_frame_count"] == 2
    assert manifest["max_gap_us"] >= 250000
    assert not list(directory.glob("*.jpg"))
    assert all("path" not in frame for frame in manifest["frames"])
    video = manifest["video"]
    assert video["duration_us"] == 450000
    assert video["mime_type"] == "video/mp4" and video["size_bytes"] > 0
    with av.open(video["path"]) as container:
        frames = list(container.decode(video=0))
        assert not container.streams.audio
    assert len(frames) == 2  # Not the full six-frame original or a keyframe-only copy.
    assert (frames[0].width, frames[0].height) == (32, 24)
    assert [round(f.pts * f.time_base * 1000000) for f in frames] == [0, 250000]
    mapping = json.loads(Path(video["frame_mapping_path"]).read_text())
    assert [f["frame_index"] for f in mapping["frames"]] == [2, 3]
    assert [f["source_time_us"] for f in mapping["frames"]] == [250000, 500000]
    assert [f["local_time_s"] for f in mapping["frames"]] == [0, 0.25]


def test_native_video_half_open_selection_records_actual_frame_tail(prepared):
    media, settings = prepared
    result = prepare_video_input(media, 150000, 490000, 4, 8,
                                 settings.data_dir / "tail", settings)
    assert result["video"]["source_frame_count"] == 1
    assert result["requested_range_us"] == [150000, 490000]
    assert result["source_range_us"] == [250000, 500000]
    assert result["video"]["duration_us"] == 250000


def test_native_nominal_sampling_limit_and_cancellation_are_explicit(prepared):
    media, settings = prepared
    directory = settings.data_dir / "limit"
    with pytest.raises(MediaError) as caught:
        prepare_video_input(media, 0, 1000000, 10, 5, directory, settings)
    assert caught.value.code == "sampling_limit_exceeded"
    assert not directory.exists()
    with pytest.raises(MediaError) as caught:
        prepare_video_input(media, 0, 1000000, 25, 99, directory, settings)
    assert caught.value.code == "invalid_sampling_config"
    with pytest.raises(MediaError) as caught:
        prepare_video_input(media, 0, 1000000, 1, 99, directory, settings, cancelled=lambda: True)
    assert caught.value.code == "cancelled"


def test_fractional_vfr_native_duration_accepts_only_movie_timescale_quantization(tmp_path):
    """A 1/600-second source timebase reproduces the phone-video rounding case.

There are two one-tick VFR delays. This deliberately makes the selected source
span 6,001,666 us after independent source timestamp rounding. The encoded MP4's
millisecond movie edit-list may report 6,001,000 us even though its decoded last
frame ends at 6,001,667 us. No private video is required by this regression.
"""
    settings = Settings(data_dir=tmp_path)
    settings.ensure_dirs()
    source = tmp_path / "media" / "fractional.mp4"
    with av.open(str(source), "w") as container:
        stream = container.add_stream("libx264", rate=Fraction(30000, 1001))
        stream.width, stream.height, stream.pix_fmt = 64, 48, "yuv420p"
        stream.time_base = Fraction(1, 600)
        stream.codec_context.time_base = Fraction(1, 600)
        stream.options = {"bf": "0", "crf": "10"}
        for i in range(200):
            frame = av.VideoFrame(64, 48, "rgb24")
            frame.planes[0].update(bytes((30 + i % 180, 50, 70)) * 64 * 48)
            frame.pts = 3000 + i * 20 + int(i >= 3) + int(i >= 30)
            frame.time_base = Fraction(1, 600)
            for packet in stream.encode(frame):
                container.mux(packet)
        for packet in stream.encode(None):
            container.mux(packet)
    media = {"id": "fractional", "original_path": "media/fractional.mp4"}
    media.update(prepare_media(media, settings))
    manifest = prepare_video_input(media, 100001, 6100000, 6, 160,
                                   tmp_path / "native-fractional", settings, max_width=32)
    assert manifest["input_origin_us"] == 101667
    assert manifest["source_range_us"] == [101667, 6103333]
    assert manifest["video"]["duration_us"] == 6001666
    assert manifest["video"]["source_frame_count"] == 180
    assert manifest["actual_sampling_known"] is False
    validation = manifest["video"]["duration_validation"]
    assert validation["reported_duration_basis"] == "stream_metadata_end"
    assert validation["container_duration_quantum_us"] == 1000
    assert abs(validation["decoded_minus_source_us"]) <= 2
    assert abs(validation["reported_minus_source_us"]) <= 1002
    if abs(validation["reported_minus_source_us"]) > 2:
        assert validation["accepted_container_quantization"] is True
        assert validation["reported_duration_us"] % 1000 == 0
    mapping = json.loads(Path(manifest["video"]["frame_mapping_path"]).read_text())
    assert mapping["duration_validation"] == validation
    assert mapping["mapping_precision_us"] == 2
    assert mapping["frames"][0]["frame_index"] == 3
    assert mapping["frames"][-1]["frame_index"] == 182
    with av.open(manifest["video"]["path"]) as container:
        output = list(container.decode(video=0))
    assert len(output) == 180
    assert output[0].pts == 0
    for source_frame, encoded_frame in zip(mapping["frames"], output):
        expected = source_frame["source_time_us"] - manifest["input_origin_us"]
        assert abs(round(encoded_frame.pts * encoded_frame.time_base * 1000000) - expected) <= 2


def test_duration_exception_requires_known_quantum_and_an_unchanged_decoded_tail():
    valid = {
        "output_duration_us": 6001000,
        "output_duration_basis": "stream_metadata_end",
        "output_decoded_display_duration_us": 6001667,
        "output_container_duration_us": 6001000,
        "output_stream_duration_us": 6001000,
        "output_movie_timescale": 1000,
        "output_duration_quantum_us": 1000,
    }
    accepted = _validate_native_duration(valid, 6001666)
    assert accepted["reported_minus_source_us"] == -666
    assert accepted["decoded_minus_source_us"] == 1
    assert accepted["accepted_container_quantization"] is True
    for changes in (
        {"output_duration_us": 6000000},  # Difference exceeds a movie tick.
        {"output_decoded_display_duration_us": 6001700},  # Real frame-tail change.
        {"output_decoded_display_duration_us": None},  # No evidence for the exception.
        {"output_movie_timescale": None},  # Unknown quantization must not be guessed.
        {"output_duration_us": 6001011},  # Not aligned to the declared movie tick.
    ):
        with pytest.raises(MediaError) as caught:
            _validate_native_duration({**valid, **changes}, 6001666)
        assert caught.value.code == "video_duration_mismatch"
