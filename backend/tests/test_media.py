import json
import shutil
import subprocess
from fractions import Fraction

import av
import pytest
from app.config import Settings
from app.media import MediaError, cut_clip, generate_demo, prepare_media, probe_video, sample_frames

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
