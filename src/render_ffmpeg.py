"""Render bounded-memory scenes with Pillow captions and native FFmpeg encoding."""

from __future__ import annotations

import json
import math
import subprocess
import time
from pathlib import Path

from generate_captions import get_audio_duration, validate_timing_plan
from pipeline_state import StageCache, atomic_json, file_digest
from render_video import (
    CAPTION_BOTTOM_MARGIN, CAPTION_MAX_LINES, CAPTION_SIDE_MARGIN, CAPTION_TEXT_WIDTH,
    CAPTION_TOP_RATIO, CAPTION_VERTICAL_PADDING_BOTTOM, VIDEO_HEIGHT, VIDEO_WIDTH,
    load_captions, load_script, load_timing_plan, render_caption_image,
)

FPS = 30


def probe_video(path: Path) -> dict:
    result = subprocess.run(["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)],
                            check=True, capture_output=True, text=True)
    return json.loads(result.stdout)


def validate_render(path: Path, duration: float) -> dict:
    probe = probe_video(path)
    videos = [s for s in probe["streams"] if s["codec_type"] == "video"]
    audios = [s for s in probe["streams"] if s["codec_type"] == "audio"]
    if len(videos) != 1 or len(audios) != 1:
        raise ValueError("Rendered output must contain one video and one audio stream.")
    video = videos[0]
    durations = [float(stream.get("duration", 0)) for stream in (video, audios[0])]
    if (video["width"], video["height"]) != (VIDEO_WIDTH, VIDEO_HEIGHT):
        raise ValueError("Unexpected output dimensions.")
    if video.get("avg_frame_rate") != "30/1" or any(not math.isfinite(d) or abs(d - duration) > .07 for d in durations):
        raise ValueError("Rendered output has missing frames or audio/video duration drift.")
    return {"passed": True, "video_duration": durations[0], "audio_duration": durations[1],
            "expected_duration": duration, "fps": FPS, "video_size": [VIDEO_WIDTH, VIDEO_HEIGHT]}


def run_ffmpeg(arguments: list[str]) -> None:
    result = subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *arguments],
                            capture_output=True, text=True)
    if result.returncode:
        raise RuntimeError(f"FFmpeg render failed: {result.stderr[-4000:]}")


def render_final_video(paths, resume: bool = False) -> str:
    started_at = time.perf_counter()
    script, timing, captions = load_script(paths), load_timing_plan(paths), load_captions(paths)
    duration = get_audio_duration(paths)
    validate_timing_plan(script, timing, captions, duration)
    if timing.get("audio_sha256") != file_digest(paths.audio_file):
        raise ValueError("Timing plan is stale for the current audio.")
    cache = StageCache(paths.output_dir)
    work = paths.output_dir / "render_work"
    work.mkdir(parents=True, exist_ok=True)
    layout_entries = []
    scene_files = []
    previous_frame = 0
    policy = [file_digest(Path(__file__)), file_digest(Path(__file__).with_name("render_video.py"))]

    for scene_index, block in enumerate(timing["blocks"]):
        asset = paths.assets_dir / f"{block['id']}.mp4"
        if not asset.is_file():
            raise FileNotFoundError(f"Missing visual for {block['id']}; refusing a blank fallback.")
        last_frame = round(block["end"] * FPS)
        frames = last_frame - previous_frame
        if frames < 1:
            raise ValueError("Scene shorter than one video frame.")
        scene_start = previous_frame / FPS
        previous_frame = last_frame
        scene = work / f"scene_{scene_index:03}.mp4"
        temporary = scene.with_suffix(".tmp.mp4")
        args = ["-threads", "2", "-stream_loop", "-1", "-i", str(asset)]
        filters = [f"[0:v]scale={VIDEO_WIDTH}:{VIDEO_HEIGHT}:force_original_aspect_ratio=increase,"
                   f"crop={VIDEO_WIDTH}:{VIDEO_HEIGHT},setsar=1,fps={FPS},trim=end_frame={frames},setpts=PTS-STARTPTS[v0]"]
        scene_layouts = []
        font_hashes = {}
        for index, caption in enumerate(block["captions"], start=1):
            image, layout = render_caption_image(caption["text"], script["language"])
            width, height = image.size
            top = max(80, min(int(VIDEO_HEIGHT * CAPTION_TOP_RATIO), VIDEO_HEIGHT - height - CAPTION_BOTTOM_MARGIN))
            layout.update({"index": caption["index"], "start": caption["start"], "end": caption["end"],
                           "screen_bbox": [CAPTION_SIDE_MARGIN, top, CAPTION_SIDE_MARGIN + width, top + height],
                           "screen_bottom_margin": VIDEO_HEIGHT - top - height})
            if layout["font_path"]:
                font_hashes[layout["font_path"]] = file_digest(Path(layout["font_path"]))
            png = work / f"caption_{caption['index']:03}.png"
            image.save(png)
            image.close()
            args.extend(["-threads", "1", "-i", str(png)])
            # Quantize adjacent boundaries together so a rounded scene cut has no blank subtitle frame.
            start = max(0, (round(caption["start"] * FPS) - round(scene_start * FPS)) / FPS)
            end = (round(caption["end"] * FPS) - round(scene_start * FPS)) / FPS
            filters.append(f"[v{index-1}][{index}:v]overlay={CAPTION_SIDE_MARGIN}:{top}:"
                           f"enable='gte(t,{start:.9f})*lt(t,{end:.9f})':eof_action=repeat[v{index}]")
            scene_layouts.append(layout)
        layout_entries.extend(scene_layouts)
        inputs = {"asset": file_digest(asset), "block": block, "frames": frames, "scene_start": scene_start,
                  "layout": scene_layouts, "fonts": font_hashes, "policy": policy}
        if not (resume and cache.matches(f"render_scene_{scene_index}", inputs, [scene])):
            print(f"FFmpeg scene {scene_index + 1}/{len(timing['blocks'])}: {block['id']}")
            args.extend(["-filter_complex_threads", "1", "-filter_complex", ";".join(filters),
                         "-map", f"[v{len(scene_layouts)}]", "-an", "-frames:v", str(frames),
                         "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-pix_fmt", "yuv420p",
                         "-threads", "4", str(temporary)])
            try:
                run_ffmpeg(args)
                temporary.replace(scene)
            finally:
                temporary.unlink(missing_ok=True)
            cache.record(f"render_scene_{scene_index}", inputs, [scene])
        scene_files.append(scene)

    layout_report = {
        "version": 2, "language": script["language"], "video_size": [VIDEO_WIDTH, VIDEO_HEIGHT],
        "safe_area": {"side_margin": CAPTION_SIDE_MARGIN, "bottom_margin": CAPTION_BOTTOM_MARGIN,
                      "max_lines": CAPTION_MAX_LINES, "text_width": CAPTION_TEXT_WIDTH},
        "captions": layout_entries,
        "passed": bool(layout_entries) and all(
            c["word_tokens_preserved"] and c["screen_bottom_margin"] >= CAPTION_BOTTOM_MARGIN
            and c["padding"]["bottom"] >= CAPTION_VERTICAL_PADDING_BOTTOM - 2 for c in layout_entries),
    }
    atomic_json(paths.caption_layout_file, layout_report)
    if not layout_report["passed"]:
        raise ValueError("Caption geometry safety check failed.")
    final_inputs = {"scenes": [file_digest(scene) for scene in scene_files],
                    "audio": file_digest(paths.audio_file), "layout": file_digest(paths.caption_layout_file),
                    "timing": file_digest(paths.timing_plan_file), "policy": policy}
    proof_path = paths.output_dir / "render_verification.json"
    if resume and cache.matches("render_final", final_inputs, [paths.final_video_file, proof_path]):
        validate_render(paths.final_video_file, duration)
        print(f"Resume: rendered video and verification evidence unchanged: {paths.final_video_file}")
        return str(paths.final_video_file)
    concat = work / "scenes.ffconcat"
    concat.write_text("ffconcat version 1.0\n" + "".join(f"file '{scene.name}'\n" for scene in scene_files), encoding="ascii")
    temporary = work / "final.tmp.mp4"
    try:
        run_ffmpeg(["-f", "concat", "-safe", "1", "-i", str(concat), "-i", str(paths.audio_file),
                    "-map", "0:v:0", "-map", "1:a:0", "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
                    "-t", f"{duration:.9f}", "-movflags", "+faststart", str(temporary)])
        verification = validate_render(temporary, duration)
        temporary.replace(paths.final_video_file)
    finally:
        temporary.unlink(missing_ok=True)
    verification.update({"renderer": "ffmpeg", "video_sha256": file_digest(paths.final_video_file),
                         "audio_sha256": file_digest(paths.audio_file), "scene_timing_basis": timing.get("scene_timing_basis"),
                         "caption_layout_sha256": file_digest(paths.caption_layout_file),
                         "timing_plan_sha256": file_digest(paths.timing_plan_file),
                         "elapsed_seconds": round(time.perf_counter() - started_at, 3), "resume_requested": resume})
    atomic_json(proof_path, verification)
    cache.record("render_final", final_inputs, [paths.final_video_file, proof_path])
    print(f"Created {paths.final_video_file}")
    return str(paths.final_video_file)
