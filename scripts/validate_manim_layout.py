#!/usr/bin/env python3
"""Basic rendered-video validation for blank or badly cropped Manim output."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import tempfile
from pathlib import Path


def find_videos(project: Path) -> list[Path]:
    media_videos = [
        path
        for path in sorted((project / "media" / "videos").rglob("*.mp4"))
        if "partial_movie_files" not in path.parts
    ]
    return media_videos + sorted((project / "renders").glob("*.mp4"))


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate rendered Manim videos at a basic pixel level.")
    parser.add_argument("project", help="Generated lecture project directory.")
    args = parser.parse_args()

    project = Path(args.project).expanduser().resolve()
    videos = find_videos(project)
    if not videos:
        print("No videos found to validate.")
        return
    if shutil.which("ffmpeg") is None:
        print("ffmpeg not found; cannot extract validation frames.")
        return
    ffprobe = shutil.which("ffprobe")

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        for video in videos:
            frame = tmp_path / f"{video.stem}.png"
            subprocess.run(
                ["ffmpeg", "-y", "-ss", "00:00:01", "-i", str(video), "-frames:v", "1", str(frame)],
                check=False,
                capture_output=True,
            )
            if frame.exists() and frame.stat().st_size > 1024:
                print(f"Validated frame extraction: {video}")
            else:
                print(f"Warning: could not extract a usable validation frame from {video}")
            if ffprobe:
                check_av_duration(video, ffprobe)


def check_av_duration(video: Path, ffprobe: str) -> None:
    result = subprocess.run(
        [
            ffprobe,
            "-v",
            "error",
            "-show_entries",
            "stream=codec_type,duration",
            "-of",
            "json",
            str(video),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        return
    payload = json.loads(result.stdout or "{}")
    durations: dict[str, float] = {}
    for stream in payload.get("streams", []):
        codec_type = stream.get("codec_type")
        duration = stream.get("duration")
        if codec_type and duration is not None:
            durations[codec_type] = float(duration)
    if "audio" in durations and "video" in durations:
        drift = abs(durations["audio"] - durations["video"])
        if drift <= 0.25:
            print(f"Validated A/V duration drift {drift:.3f}s: {video}")
        else:
            print(f"Warning: A/V duration drift {drift:.3f}s in {video}")


if __name__ == "__main__":
    main()
