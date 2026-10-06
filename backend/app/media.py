"""Media operations on the original presentation timeline (no database writes).

Frame indexes are immutable. Business time zero is the first decoded display PTS,
not container start_time or frame_number / average_fps. Intervals are half-open;
sampling and clipping select frames whose presentation starts inside that interval.
"""
from __future__ import annotations

import bisect
import json
import math
import os
import shutil
import subprocess
import time
import uuid
from collections.abc import Callable
from fractions import Fraction
from functools import lru_cache
from itertools import pairwise
from pathlib import Path

import av

from .config import Settings

Cancelled = Callable[[], bool] | None
MICROSECOND = 1_000_000
INDEX_VERSION = 1
OUTPUT_MOVIE_TIMESCALE = 1000  # MP4 movie/edit-list duration ticks, not video frame PTS ticks.
FRAME_MAPPING_TOLERANCE_US = 2


class MediaError(RuntimeError):
    def __init__(self, code: str, message: str, details: dict | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}


def _check_cancelled(cancelled: Cancelled) -> None:
    if cancelled and cancelled():
        raise MediaError("cancelled", "Media processing was cancelled")


def _us(value: Fraction) -> int:
    return round(value * MICROSECOND)


def _run(command: list[str], cancelled: Cancelled = None, timeout: int = 7200) -> str:
    _check_cancelled(cancelled)
    if not shutil.which(command[0]):
        raise MediaError("tool_missing", f"Required executable is not available: {command[0]}")
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    started = time.monotonic()
    try:
        while True:
            _check_cancelled(cancelled)
            if time.monotonic() - started > timeout:
                raise MediaError("media_timeout", "Media subprocess exceeded its timeout")
            try:
                stdout, stderr = process.communicate(timeout=0.2)
                break
            except subprocess.TimeoutExpired:
                continue
        if process.returncode:
            raise MediaError("media_process_failed", f"{command[0]} failed", {
                "returncode": process.returncode,
                "stderr": stderr.decode("utf-8", errors="replace")[-4000:],
            })
        return stdout.decode("utf-8", errors="replace")
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.communicate(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                process.communicate()


def _under_root(path: str | Path, settings: Settings) -> Path:
    root = settings.data_dir.resolve()
    candidate = Path(path)
    if not candidate.is_absolute():
        candidate = root / candidate
    candidate = candidate.resolve()
    if not candidate.is_relative_to(root):
        raise MediaError("invalid_media_path", "Media path must stay inside the data directory")
    return candidate


def _relative(path: Path, settings: Settings) -> str:
    return str(path.resolve().relative_to(settings.data_dir.resolve()))


def _source(media: dict, settings: Settings) -> Path:
    if not media.get("original_path"):
        raise MediaError("media_not_found", "Media has no original file")
    path = _under_root(media["original_path"], settings)
    if not path.is_file():
        raise MediaError("media_not_found", "Original media file is missing")
    return path


def probe_video(path: Path) -> dict:
    """Fast metadata probe. prepare_media replaces duration with decoded PTS duration."""
    try:
        result = json.loads(_run([
            "ffprobe", "-v", "error", "-select_streams", "v:0", "-show_streams",
            "-show_format", "-of", "json", str(path),
        ], timeout=60))
        streams = result.get("streams", [])
        if not streams:
            raise MediaError("media_invalid", "Input does not contain a video stream")
        stream = streams[0]
        width, height = int(stream["width"]), int(stream["height"])
        if width < 2 or height < 2:
            raise MediaError("media_invalid", "Video dimensions are invalid")
        try:
            rate = Fraction(stream.get("avg_frame_rate", "0/1"))
        except (ValueError, ZeroDivisionError):
            rate = Fraction(0, 1)  # Unknown FPS is legal; decoded PTS remains authoritative.
        rotation = float(stream.get("tags", {}).get("rotate", 0))
        for side in stream.get("side_data_list", []):
            if "rotation" in side:
                rotation = float(side["rotation"])
        rotation = round(rotation) % 360
        duration = stream.get("duration", result.get("format", {}).get("duration"))
        return {
            "duration_us": round(float(duration or 0) * MICROSECOND),
            "width": width, "height": height, "fps": float(rate),
            "rotation": rotation, "codec": stream.get("codec_name"),
            "time_base": stream.get("time_base"),
            "display_width": height if rotation in (90, 270) else width,
            "display_height": width if rotation in (90, 270) else height,
            "duration_is_provisional": True,
        }
    except MediaError:
        raise
    except (ValueError, KeyError, TypeError, ZeroDivisionError) as exc:
        raise MediaError("media_invalid", f"Invalid video metadata: {exc}") from exc


def _build_frame_index(path: Path, media_id: str, cancelled: Cancelled = None) -> dict:
    records = []
    origin = None
    last_duration = None
    stream_end = None
    container_duration_us = None
    try:
        with av.open(str(path)) as container:
            container_duration_us = container.duration
            stream = container.streams.video[0]
            if stream.duration is not None and stream.start_time is not None:
                stream_end = (stream.start_time + stream.duration) * stream.time_base
            for number, frame in enumerate(container.decode(stream)):
                _check_cancelled(cancelled)
                if frame.pts is None or frame.time_base is None:
                    raise MediaError("missing_frame_timestamp", "Decoded frame has no display PTS")
                stamp = frame.pts * frame.time_base
                if origin is None:
                    origin = stamp
                source_time = _us(stamp - origin)
                if records and source_time <= records[-1]["source_time_us"]:
                    raise MediaError("non_monotonic_timestamps", "Display timestamps are not unique and increasing")
                records.append({
                    "frame_id": f"{media_id}:frame:{number}", "frame_index": number,
                    "pts": frame.pts, "time_base": str(frame.time_base),
                    "source_time_us": source_time, "key_frame": bool(frame.key_frame),
                })
                raw_duration = getattr(frame, "duration", 0)
                last_duration = raw_duration * frame.time_base if raw_duration else None
        if not records:
            raise MediaError("media_invalid", "Input contains no decodable video frames")
        for current, following in pairwise(records):
            current["duration_us"] = following["source_time_us"] - current["source_time_us"]
        last_stamp = records[-1]["pts"] * Fraction(records[-1]["time_base"])
        # Stream end is sometimes more reliable than decoder duration on the final VFR frame.
        estimated = False
        if stream_end is not None and stream_end > last_stamp:
            tail = stream_end - last_stamp
            duration_basis = "stream_metadata_end"
        elif last_duration and last_duration > 0:
            tail = last_duration
            duration_basis = "decoded_last_frame_duration"
        else:
            estimated = True
            duration_basis = "estimated_last_frame_duration"
            deltas = [r["duration_us"] for r in records[:-1]]
            tail = Fraction(sorted(deltas)[len(deltas) // 2], MICROSECOND) if deltas else Fraction(1, 30)
        records[-1]["duration_us"] = max(1, _us(tail))
        for record in records:
            record["source_end_us"] = record["source_time_us"] + record["duration_us"]
        return {
            "version": INDEX_VERSION, "media_id": media_id, "frames": records,
            "frame_count": len(records), "duration_us": records[-1]["source_end_us"],
            "source_origin_pts": records[0]["pts"],
            "source_time_base": records[0]["time_base"],
            "last_frame_duration_estimated": estimated,
            "duration_basis": duration_basis,
            "container_duration_us": container_duration_us,
            "stream_duration_us": _us(stream_end - origin) if stream_end is not None else None,
            # Keep this independent from the index's stream-metadata-based final end.
            # MP4 edit lists can quantize stream duration while decoded frame PTS and
            # packet/frame durations still preserve the fine-grained presentation span.
            "decoded_display_duration_us": (
                _us(last_stamp + last_duration - origin) if last_duration and last_duration > 0 else None
            ),
        }
    except MediaError:
        raise
    except (av.FFmpegError, IndexError, OSError) as exc:
        raise MediaError("decode_failed", f"Could not index video: {exc}") from exc


def _write_json(path: Path, data: dict) -> None:
    temp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temp.open("w") as handle:
            json.dump(data, handle, separators=(",", ":"))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def prepare_media(media: dict, settings: Settings, cancelled: Cancelled = None) -> dict:
    """Return media updates; callers alone own database state/publication."""
    source = _source(media, settings)
    info = probe_video(source)
    if info["rotation"] not in (0, 90, 180, 270):
        raise MediaError("unsupported_rotation", "Only right-angle video rotation is supported")
    # Unique preparation directories prevent stale attempts from replacing published assets.
    directory = settings.data_dir / "media" / f"prepared-{uuid.uuid4().hex}"
    directory.mkdir(parents=True)
    try:
        index = _build_frame_index(source, str(media["id"]), cancelled)
        index.update(rotation=info["rotation"], original_path=media["original_path"])
        frame_path = directory / "frames.json"
        _write_json(frame_path, index)
        preview = directory / "preview.mp4"
        _run([
            "ffmpeg", "-nostdin", "-v", "error", "-y", "-i", str(source),
            "-map", "0:v:0", "-an", "-vf",
            "setpts=PTS-STARTPTS,scale=w='trunc(min(1280,iw)/2)*2':h=-2,setsar=1",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "25", "-pix_fmt", "yuv420p",
            "-fps_mode", "vfr", "-enc_time_base", "1:1000000",
            "-video_track_timescale", "1000000", "-movflags", "+faststart", str(preview),
        ], cancelled)
        preview_index = _build_frame_index(preview, str(media["id"]), cancelled)
        if preview_index["frame_count"] != index["frame_count"]:
            raise MediaError("preview_mapping_failed", "Preview changed the number of display frames")
        if any(abs(p["source_time_us"] - s["source_time_us"]) > 2
               for p, s in zip(preview_index["frames"], index["frames"])):
            raise MediaError("preview_mapping_failed", "Preview changed source frame timing")
        mapping = {
            "purpose": "navigation_only", "audio_included": False,
            "frames": [
                {"preview_time_us": p["source_time_us"], "source_time_us": s["source_time_us"],
                 "source_frame_id": s["frame_id"]}
                for p, s in zip(preview_index["frames"], index["frames"])
            ],
        }
        mapping_path = directory / "preview_mapping.json"
        _write_json(mapping_path, mapping)
        _check_cancelled(cancelled)
        return {
            **info, "duration_us": index["duration_us"], "duration_is_provisional": False,
            "preview_path": _relative(preview, settings),
            "frame_index_path": _relative(frame_path, settings),
            "preview_mapping_path": _relative(mapping_path, settings),
            "frame_count": index["frame_count"],
            "source_origin_pts": index["source_origin_pts"],
            "source_time_base": index["source_time_base"],
            "last_frame_duration_estimated": index["last_frame_duration_estimated"],
            "preview_audio_included": False,
        }
    except BaseException:
        shutil.rmtree(directory, ignore_errors=True)
        raise


@lru_cache(maxsize=2)
def _read_index(path: str) -> dict:
    with Path(path).open() as handle:
        return json.load(handle)


def _index(media: dict, settings: Settings) -> dict:
    if not media.get("frame_index_path"):
        raise MediaError("media_not_prepared", "Prepare media before sampling or clipping")
    path = _under_root(media["frame_index_path"], settings)
    try:
        index = _read_index(str(path))
    except (OSError, ValueError) as exc:
        raise MediaError("frame_index_missing", "Frame index is missing or invalid") from exc
    if index.get("version") != INDEX_VERSION or index.get("media_id") != media["id"]:
        raise MediaError("frame_index_mismatch", "Frame index belongs to another media/version")
    return index


def _range(index: dict, start_us: int, end_us: int) -> tuple[int, int]:
    if isinstance(start_us, bool) or isinstance(end_us, bool) or not isinstance(start_us, int) or not isinstance(end_us, int):
        raise MediaError("invalid_time_range", "Time boundaries must be integer microseconds")
    if not 0 <= start_us < end_us <= index["duration_us"]:
        raise MediaError("invalid_time_range", "Requested interval is outside the source video", {
            "requested_range_us": [start_us, end_us], "duration_us": index["duration_us"],
        })
    times = [frame["source_time_us"] for frame in index["frames"]]
    return bisect.bisect_left(times, start_us), bisect.bisect_left(times, end_us)


def _display_frame(frame: av.VideoFrame, rotation: int, max_width: int) -> av.VideoFrame:
    if rotation:
        graph = av.filter.Graph()
        first = graph.add_buffer(template=frame)
        if rotation == 180:
            horizontal = graph.add("hflip")
            vertical = graph.add("vflip")
            first.link_to(horizontal)
            horizontal.link_to(vertical)
            last = vertical
        else:
            # ffprobe display-matrix rotations are counterclockwise degrees.
            last = graph.add("transpose", "cclock" if rotation == 90 else "clock")
            first.link_to(last)
        sink = graph.add("buffersink")
        last.link_to(sink)
        graph.configure()
        graph.push(frame)
        frame = graph.pull()
    width = min(max_width, frame.width)
    width = max(2, width // 2 * 2)
    height = max(2, round(frame.height * width / frame.width) // 2 * 2)
    return frame.reformat(width=width, height=height, format="yuvj420p")


def _save_jpeg(frame: av.VideoFrame, path: Path) -> None:
    codec = av.CodecContext.create("mjpeg", "w")
    codec.width, codec.height, codec.pix_fmt = frame.width, frame.height, "yuvj420p"
    codec.time_base = Fraction(1, 1)
    frame.pts, frame.time_base = 0, Fraction(1, 1)
    temp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temp.open("wb") as handle:
            for packet in codec.encode(frame):
                handle.write(bytes(packet))
            for packet in codec.encode(None):
                handle.write(bytes(packet))
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def sample_frames(media: dict, start_us: int, end_us: int, fps: float,
                  max_frames: int, output_dir: Path, settings: Settings,
                  max_width: int = 768, cancelled: Cancelled = None) -> dict:
    """Sample first real frame at/after each grid time; never duplicate observations."""
    _check_cancelled(cancelled)
    if not math.isfinite(fps) or fps <= 0 or fps > 1000 or max_frames < 1 or max_width < 2:
        raise MediaError("invalid_sampling_config", "Invalid sampling FPS, limit or dimensions")
    index = _index(media, settings)
    first, stop = _range(index, start_us, end_us)
    records = index["frames"]
    times = [r["source_time_us"] for r in records]
    step = Fraction(MICROSECOND, 1) / Fraction(str(fps))
    chosen = []
    tick = Fraction(start_us, 1)
    while tick < end_us:
        position = bisect.bisect_left(times, tick, lo=first, hi=stop)
        if position < stop and (not chosen or position != chosen[-1]):
            chosen.append(position)
            if len(chosen) > max_frames:
                raise MediaError("sampling_limit_exceeded", "Requested sampling exceeds max_frames; split the window", {
                    "max_frames": max_frames, "requested_fps": fps,
                    "minimum_required_frames": len(chosen),
                })
        tick += step
    if not chosen:
        raise MediaError("no_observed_frames", "No original frame starts inside the requested interval")
    directory = _under_root(output_dir, settings)
    directory.mkdir(parents=True, exist_ok=True)
    requested = {records[n]["source_time_us"]: records[n] for n in chosen}
    origin = records[0]["pts"] * Fraction(records[0]["time_base"])
    frames = []
    try:
        with av.open(str(_source(media, settings))) as container:
            stream = container.streams.video[0]
            seek_stamp = records[chosen[0]]["pts"] * Fraction(records[chosen[0]]["time_base"])
            container.seek(math.floor(seek_stamp / stream.time_base), stream=stream, backward=True)
            for frame in container.decode(stream):
                _check_cancelled(cancelled)
                if frame.pts is None:
                    raise MediaError("missing_frame_timestamp", "Decoded frame has no display PTS")
                stamp = _us(frame.pts * frame.time_base - origin)
                if stamp > records[chosen[-1]]["source_time_us"]:
                    break
                record = requested.get(stamp)
                if record is None:
                    continue
                path = directory / f"frame_{record['frame_index']:09d}.jpg"
                displayed = _display_frame(frame, index.get("rotation", 0), max_width)
                _save_jpeg(displayed, path)
                frames.append({
                    **record, "path": str(path.resolve()),
                    "local_time_s": (stamp - records[chosen[0]]["source_time_us"]) / MICROSECOND,
                    "width": displayed.width, "height": displayed.height,
                })
                if len(frames) == len(chosen):
                    break
    except MediaError:
        raise
    except (av.FFmpegError, OSError) as exc:
        raise MediaError("decode_failed", f"Frame sampling failed: {exc}") from exc
    if len(frames) != len(chosen):
        raise MediaError("frame_mapping_failed", "Decoder did not reproduce all indexed frames", {
            "expected": len(chosen), "decoded": len(frames),
        })
    observed = [r["source_time_us"] for r in frames]
    edges = [start_us, *observed, end_us]
    return {
        "frames": frames, "input_origin_us": observed[0],
        "source_range_us": [start_us, end_us], "read_start_us": start_us, "read_end_us": end_us,
        "max_gap_us": max(b - a for a, b in pairwise(edges)),
        "requested_fps": fps, "sample_fps": fps, "input_mode": "images",
        "actual_sampling_known": True,
        "sampling_policy": "first_source_frame_at_or_after_grid_time_unique",
        "input_duration_s": (end_us - observed[0]) / MICROSECOND,
    }


def cut_clip(media: dict, start_us: int, end_us: int, output_path: Path,
             settings: Settings, cancelled: Cancelled = None,
             *, max_width: int | None = None) -> dict:
    _check_cancelled(cancelled)
    index = _index(media, settings)
    first, stop = _range(index, start_us, end_us)
    if first == stop:
        raise MediaError("no_observed_frames", "No original frame starts inside this clip interval")
    records = index["frames"]
    selected = records[first:stop]
    begin, last = selected[0], selected[-1]
    tb = Fraction(begin["time_base"])
    origin = records[0]["pts"] * Fraction(records[0]["time_base"])
    # The source's last display duration is retained when the cut reaches EOF.
    end_stamp = (records[stop]["pts"] * Fraction(records[stop]["time_base"])) if stop < len(records) else (
        origin + Fraction(last["source_end_us"], MICROSECOND)
    )
    end_pts = math.ceil(end_stamp / tb)
    preceding_keys = [r for r in records[:first + 1] if r["key_frame"]]
    key = preceding_keys[-1] if preceding_keys else records[0]
    seek_seconds = float(key["pts"] * Fraction(key["time_base"]))
    if max_width is not None and (not isinstance(max_width, int) or max_width < 2):
        raise MediaError("invalid_sampling_config", "Video width limit must be at least two pixels")
    scale = ("scale=w='trunc(iw/2)*2':h='trunc(ih/2)*2'" if max_width is None else
             f"scale=w='trunc(min({max_width},iw)/2)*2':h=-2")
    destination = _under_root(output_path, settings)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.stem}.{uuid.uuid4().hex}.mp4")
    try:
        _run([
            "ffmpeg", "-nostdin", "-v", "error", "-y", "-copyts",
            # Decode from the indexed keyframe, then let trim choose exact source PTS.
            # Disabling input accurate_seek avoids its additional start-time adjustment
            # on containers with a nonzero initial timestamp (e.g. MP4 edit lists).
            "-seek_timestamp", "1", "-ss", f"{seek_seconds:.9f}", "-noaccurate_seek",
            "-i", str(_source(media, settings)), "-map", "0:v:0", "-an", "-vf",
            (f"settb=expr={tb.numerator}/{tb.denominator},"
             f"trim=start_pts={begin['pts']}:end_pts={end_pts},setpts=PTS-STARTPTS,"
             f"{scale},setsar=1"),
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
            "-fps_mode", "vfr", "-enc_time_base", "1:1000000",
            "-frames:v", str(len(selected)), "-video_track_timescale", "1000000",
            "-movie_timescale", str(OUTPUT_MOVIE_TIMESCALE),
            "-movflags", "+faststart", str(temporary),
        ], cancelled)
        actual = _build_frame_index(temporary, str(media["id"]), cancelled)
        if actual["frame_count"] != len(selected):
            raise MediaError("clip_frame_mismatch", "Encoded clip does not contain the requested source frames", {
                "expected_frame_count": len(selected), "output_frame_count": actual["frame_count"],
            })
        expected_times = [r["source_time_us"] - begin["source_time_us"] for r in selected]
        if any(abs(e - r["source_time_us"]) > FRAME_MAPPING_TOLERANCE_US
               for e, r in zip(expected_times, actual["frames"])):
            raise MediaError("clip_timestamp_mismatch", "Encoded clip changed source frame timing")
        _check_cancelled(cancelled)
        os.replace(temporary, destination)
        return {
            "path": _relative(destination, settings),
            "requested_range_us": [start_us, end_us],
            "actual_range_us": [begin["source_time_us"], last["source_end_us"]],
            "source_first_frame": {k: begin[k] for k in ("frame_id", "frame_index", "pts", "time_base", "source_time_us")},
            "source_last_frame": {k: last[k] for k in ("frame_id", "frame_index", "pts", "time_base", "source_time_us", "source_end_us")},
            "frame_count": len(selected), "output_duration_us": actual["duration_us"],
            "output_origin_pts": actual["source_origin_pts"],
            "output_time_base": actual["source_time_base"],
            "output_duration_basis": actual["duration_basis"],
            "output_decoded_display_duration_us": actual["decoded_display_duration_us"],
            "output_stream_duration_us": actual["stream_duration_us"],
            "output_container_duration_us": actual["container_duration_us"],
            "output_movie_timescale": OUTPUT_MOVIE_TIMESCALE,
            "output_duration_quantum_us": MICROSECOND // OUTPUT_MOVIE_TIMESCALE,
            "encoding": "h264/yuv420p; decoded-and-reencoded; variable-frame-timing",
            "audio_included": False, "frame_selection": "presentation_start_in_half_open_range",
        }
    finally:
        temporary.unlink(missing_ok=True)


def _validate_native_duration(clipped: dict, source_duration_us: int) -> dict:
    """Allow proven MP4 duration quantization, never a changed frame timeline.

cut_clip has already verified every frame PTS and count. In addition, where the
decoder exposes the final display duration, it must agree with the source span
within the same two-microsecond rounding tolerance. Only then can millisecond-
aligned stream metadata differ by one explicitly configured movie-timescale tick.
"""
    reported = clipped["output_duration_us"]
    decoded = clipped.get("output_decoded_display_duration_us")
    reported_error = reported - source_duration_us
    decoded_error = decoded - source_duration_us if decoded is not None else None
    quantum = clipped.get("output_duration_quantum_us")
    known_movie_quantum = (clipped.get("output_movie_timescale") == OUTPUT_MOVIE_TIMESCALE
                          and quantum == MICROSECOND // OUTPUT_MOVIE_TIMESCALE)
    quantized = (
        abs(reported_error) > FRAME_MAPPING_TOLERANCE_US
        and known_movie_quantum
        and clipped.get("output_duration_basis") == "stream_metadata_end"
        and reported % quantum == 0
        and decoded_error is not None
        and abs(decoded_error) <= FRAME_MAPPING_TOLERANCE_US
        and abs(reported_error) <= quantum + FRAME_MAPPING_TOLERANCE_US
    )
    validation = {
        "source_display_duration_us": source_duration_us,
        "reported_duration_us": reported,
        "reported_duration_basis": clipped.get("output_duration_basis"),
        "decoded_display_duration_us": decoded,
        "container_duration_us": clipped.get("output_container_duration_us"),
        "stream_duration_us": clipped.get("output_stream_duration_us"),
        "reported_minus_source_us": reported_error,
        "decoded_minus_source_us": decoded_error,
        "frame_timing_tolerance_us": FRAME_MAPPING_TOLERANCE_US,
        "container_duration_quantum_us": quantum if known_movie_quantum else None,
        "accepted_container_quantization": bool(quantized),
    }
    if ((decoded_error is not None and abs(decoded_error) > FRAME_MAPPING_TOLERANCE_US)
            or (abs(reported_error) > FRAME_MAPPING_TOLERANCE_US and not quantized)):
        raise MediaError("video_duration_mismatch", "Encoded model input changed source display duration", validation)
    return validation


def prepare_video_input(media: dict, start_us: int, end_us: int, fps: float,
                        max_frames: int, output_dir: Path, settings: Settings,
                        max_width: int = 768, cancelled: Cancelled = None) -> dict:
    """Encode only this input window, retaining source VFR timing and clip-local zero.

`frames` is a NOMINAL local index for diagnostics/fixture mode, not a claim that
the provider sampled those frames. Video evidence must use clip-local timestamps.
The full original-to-clip frame mapping is persisted separately without JPEGs.
"""
    _check_cancelled(cancelled)
    if not math.isfinite(fps) or fps <= 0 or fps > 24 or max_frames < 1 or max_width < 2:
        raise MediaError("invalid_sampling_config", "Native video sampling requires FPS in (0, 24]")
    index = _index(media, settings)
    first, stop = _range(index, start_us, end_us)
    selected = index["frames"][first:stop]
    if not selected:
        raise MediaError("no_observed_frames", "No original frame starts inside this input window")
    origin = selected[0]["source_time_us"]
    actual_end = selected[-1]["source_end_us"]
    duration = actual_end - origin
    nominal_count = math.ceil(Fraction(duration, MICROSECOND) * Fraction(str(fps)))
    if nominal_count > max_frames:
        raise MediaError("sampling_limit_exceeded", "Native video FPS exceeds the nominal frame budget; split the window", {
            "max_frames": max_frames, "requested_fps": fps,
            "minimum_required_frames": nominal_count,
        })
    directory = _under_root(output_dir, settings)
    directory.mkdir(parents=True, exist_ok=True)
    # The caller's attempt directory plus a unique filename prevents stale publication.
    token = uuid.uuid4().hex
    video_path = directory / f"input_{token}.mp4"
    mapping_path = directory / f"input_{token}_mapping.json"
    try:
        clipped = cut_clip(media, start_us, end_us, video_path, settings,
                           cancelled=cancelled, max_width=max_width)
        if clipped["output_origin_pts"] != 0:
            raise MediaError("video_origin_mismatch", "Encoded model input does not start at timestamp zero")
        duration_validation = _validate_native_duration(clipped, duration)
        frame_mapping = [{
            **record, "local_time_s": (record["source_time_us"] - origin) / MICROSECOND,
        } for record in selected]
        _write_json(mapping_path, {
            "input_origin_us": origin, "requested_range_us": [start_us, end_us],
            "actual_range_us": clipped["actual_range_us"],
            "frames": frame_mapping, "mapping_precision_us": FRAME_MAPPING_TOLERANCE_US,
            "duration_validation": duration_validation,
        })
        # This index estimates which source frames a sampling grid could observe. It
        # remains local; native decoding/sampling inside the provider is not exposed.
        times = [record["source_time_us"] for record in selected]
        nominal = []
        tick, step = Fraction(origin), Fraction(MICROSECOND) / Fraction(str(fps))
        while tick < actual_end:
            position = bisect.bisect_left(times, tick)
            if position < len(selected) and (not nominal or nominal[-1] != position):
                nominal.append(position)
            tick += step
        gaps = [b - a for a, b in pairwise([*times, actual_end])]
        _check_cancelled(cancelled)
        return {
            "input_mode": "video", "input_origin_us": origin,
            "source_range_us": clipped["actual_range_us"],
            "requested_range_us": [start_us, end_us],
            "read_start_us": start_us, "read_end_us": end_us,
            "input_duration_s": duration / MICROSECOND,
            "requested_fps": fps, "sample_fps": fps,
            "nominal_frame_count": nominal_count,
            "frames": [frame_mapping[position] for position in nominal],
            "max_gap_us": max(math.ceil(step), max(gaps)),
            "actual_sampling_known": False,
            "frame_manifest_role": "nominal_source_index_not_provider_observation",
            "sampling_policy": "provider_native_video_requested_fps",
            "video": {
                "path": str(video_path.resolve()), "mime_type": "video/mp4",
                "duration_us": duration, "size_bytes": video_path.stat().st_size,
                "actual_range_us": clipped["actual_range_us"],
                "frame_mapping_path": str(mapping_path.resolve()),
                "source_frame_count": len(selected), "audio_included": False,
                "duration_validation": duration_validation,
            },
        }
    except BaseException:
        video_path.unlink(missing_ok=True)
        mapping_path.unlink(missing_ok=True)
        raise


_FONT = {
    "A": [14,17,17,31,17,17,17], "C": [14,17,16,16,16,17,14],
    "D": [30,17,17,17,17,17,30], "E": [31,16,16,30,16,16,31],
    "F": [31,16,16,30,16,16,16], "I": [31,4,4,4,4,4,31],
    "M": [17,27,21,21,17,17,17], "N": [17,25,21,19,17,17,17],
    "O": [14,17,17,17,17,17,14], "R": [30,17,17,30,20,18,17],
    "S": [15,16,16,14,1,1,30], "T": [31,4,4,4,4,4,4],
}


def _paint_rect(pixels: bytearray, width: int, height: int, x: int, y: int,
                w: int, h: int, color: tuple[int, int, int]) -> None:
    left, right = max(0, x), min(width, x + w)
    if left >= right:
        return
    row = bytes(color) * (right - left)
    for yy in range(max(0, y), min(height, y + h)):
        offset = (yy * width + left) * 3
        pixels[offset:offset + len(row)] = row


def _paint_text(pixels: bytearray, width: int, height: int, text: str, y: int) -> None:
    for column, char in enumerate(text):
        for row, bits in enumerate(_FONT.get(char, [0] * 7)):
            for bit in range(5):
                if bits & (1 << (4 - bit)):
                    _paint_rect(pixels, width, height, 8 + column * 12 + bit * 2,
                                y + row * 2, 2, 2, (245, 245, 245))


def generate_demo(output_path: Path) -> dict:
    """Synthetic 12-second fixture. Its labels test plumbing, never AI accuracy."""
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = output_path.with_name(f".{output_path.stem}.{uuid.uuid4().hex}.mp4")
    width, height, fps = 320, 180, 24
    background = bytearray(bytes((20, 26, 38)) * width * height)
    _paint_text(background, width, height, "DETERMINISTIC DEMO", 8)
    _paint_text(background, width, height, "NOT AI INFERENCE", 156)
    _paint_rect(background, width, height, 0, 131, width, 2, (110, 125, 145))
    try:
        with av.open(str(temporary), "w") as container:
            stream = container.add_stream("libx264", rate=fps)
            stream.width, stream.height, stream.pix_fmt = width, height, "yuv420p"
            stream.time_base = Fraction(1, fps)
            for number in range(12 * fps):
                t = number / fps
                pixels = background.copy()
                _paint_rect(pixels, width, height, 0, 146, round(width * t / 12), 3, (120, 145, 180))
                if 3 <= t < 5 or 8 <= t < 10:
                    _paint_rect(pixels, width, height, 220, 65, 48, 48, (35, 195, 120))
                contact = 3 if t < 6 else 9
                cy = round(123 - min(85, 12 * (t - contact) ** 2))
                for dy in range(-8, 9):
                    span = math.floor(math.sqrt(64 - dy * dy))
                    _paint_rect(pixels, width, height, 100 - span, cy + dy, 2 * span + 1, 1, (250, 175, 60))
                frame = av.VideoFrame(width, height, "rgb24")
                # PyAV planes may have padded rows; preserve their real line size.
                plane = frame.planes[0]
                if plane.line_size == width * 3:
                    plane.update(pixels)
                else:
                    padded = bytearray(plane.buffer_size)
                    for row in range(height):
                        padded[row * plane.line_size:row * plane.line_size + width * 3] = pixels[row * width * 3:(row + 1) * width * 3]
                    plane.update(padded)
                frame.pts, frame.time_base = number, Fraction(1, fps)
                for packet in stream.encode(frame):
                    container.mux(packet)
            for packet in stream.encode(None):
                container.mux(packet)
        os.replace(temporary, output_path)
    finally:
        temporary.unlink(missing_ok=True)
    return {
        **probe_video(output_path), "is_demo": True,
        "fixture_description": "DETERMINISTIC DEMO — synthetic plumbing fixture, not AI accuracy evidence",
        "fixture_events": [
            {"event_id": "fixture_interval_1", "kind": "interval", "start_us": 3_000_000, "end_us": 5_000_000, "entity_key": "demo_shape", "description": "Green square visible"},
            {"event_id": "fixture_interval_2", "kind": "interval", "start_us": 8_000_000, "end_us": 10_000_000, "entity_key": "demo_shape", "description": "Green square visible"},
            {"event_id": "fixture_point_1", "kind": "point", "anchor_us": 3_000_000, "entity_key": "demo_ball", "description": "Synthetic ball touches floor"},
            {"event_id": "fixture_point_2", "kind": "point", "anchor_us": 9_000_000, "entity_key": "demo_ball", "description": "Synthetic ball touches floor"},
        ],
    }
