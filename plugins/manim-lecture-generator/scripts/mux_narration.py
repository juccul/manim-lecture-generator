#!/usr/bin/env python3
"""Mux generated narration audio with the rendered Manim video."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import wave
from pathlib import Path


def concat_wavs(paths: list[Path], output: Path) -> bool:
    if not paths:
        return False
    with wave.open(str(paths[0]), "rb") as first:
        params = first.getparams()
        frames = [first.readframes(first.getnframes())]
    for path in paths[1:]:
        with wave.open(str(path), "rb") as handle:
            if handle.getparams()[:3] != params[:3]:
                return False
            frames.append(handle.readframes(handle.getnframes()))
    output.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(output), "wb") as target:
        target.setparams(params)
        for frame_data in frames:
            target.writeframes(frame_data)
    return True


def wav_duration(path: Path) -> float:
    with wave.open(str(path), "rb") as handle:
        return handle.getnframes() / float(handle.getframerate())


def concat_wavs_with_timing(items: list[dict], output: Path) -> bool:
    audio_items = [item for item in items if item.get("audio")]
    if not audio_items:
        return False

    first_path = Path(audio_items[0]["audio"])
    with wave.open(str(first_path), "rb") as first:
        params = first.getparams()

    output.parent.mkdir(parents=True, exist_ok=True)
    cursor = 0.0
    with wave.open(str(output), "wb") as target:
        target.setparams(params)
        silence_frame = b"\x00" * params.sampwidth * params.nchannels
        for item in audio_items:
            start = float(item.get("start", cursor))
            if start > cursor:
                gap_frames = int(round((start - cursor) * params.framerate))
                target.writeframes(silence_frame * gap_frames)
                cursor += gap_frames / params.framerate

            path = Path(item["audio"])
            with wave.open(str(path), "rb") as handle:
                if handle.getparams()[:3] != params[:3]:
                    return False
                frames = handle.readframes(handle.getnframes())
                target.writeframes(frames)
                actual_duration = handle.getnframes() / float(handle.getframerate())

            declared_duration = float(item.get("duration", actual_duration))
            cursor += actual_duration
            if declared_duration > actual_duration:
                pad_frames = int(round((declared_duration - actual_duration) * params.framerate))
                target.writeframes(silence_frame * pad_frames)
                cursor += pad_frames / params.framerate
    return True


def find_video(project: Path) -> Path | None:
    candidates = sorted((project / "media" / "videos").rglob("Lecture01.mp4"))
    return candidates[-1] if candidates else None


def main() -> None:
    parser = argparse.ArgumentParser(description="Mux narration audio into a rendered Manim video.")
    parser.add_argument("project", help="Generated lecture project directory.")
    args = parser.parse_args()

    project = Path(args.project).expanduser().resolve()
    timing = json.loads((project / "narration" / "timing.json").read_text(encoding="utf-8"))
    combined = project / "assets" / "audio" / "lecture_01.wav"
    if not concat_wavs_with_timing(timing, combined):
        print("Could not concatenate WAV files; skipping mux.")
        return

    video = find_video(project)
    if not video:
        print("Rendered Manim video not found; skipping mux.")
        return
    if shutil.which("ffmpeg") is None:
        print(f"ffmpeg not found. Combined narration is at {combined}.")
        return

    output = project / "renders" / "lecture_01_narrated.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-y",
            "-i",
            str(video),
            "-i",
            str(combined),
            "-map",
            "0:v:0",
            "-map",
            "1:a:0",
            "-c:v",
            "copy",
            "-c:a",
            "aac",
            "-shortest",
            str(output),
        ],
        check=False,
    )
    print(f"Narrated video: {output}")


if __name__ == "__main__":
    main()
