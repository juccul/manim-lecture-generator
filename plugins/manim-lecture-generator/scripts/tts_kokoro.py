#!/usr/bin/env python3
"""Generate local TTS audio for narration timing beats using Kokoro."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import wave
from pathlib import Path


def write_silence(path: Path, duration: float, sample_rate: int = 24000) -> None:
    frames = int(max(duration, 0.25) * sample_rate)
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(sample_rate)
        handle.writeframes(b"\x00\x00" * frames)


def wav_duration(path: Path) -> float:
    with wave.open(str(path), "rb") as handle:
        return handle.getnframes() / float(handle.getframerate())


def kokoro_lang_code(voice: str) -> str:
    if voice.startswith("b"):
        return "b"
    if voice.startswith("a"):
        return "a"
    return "a"


def generate_with_kokoro(text: str, path: Path, voice: str) -> float:
    from kokoro import KPipeline
    import soundfile as sf

    pipeline = KPipeline(lang_code=kokoro_lang_code(voice))
    chunks = list(pipeline(text, voice=voice))
    if not chunks:
        raise RuntimeError("Kokoro returned no audio chunks")
    audio = chunks[0][2]
    sf.write(str(path), audio, 24000)
    return len(audio) / 24000.0


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate beat audio with local Kokoro TTS.")
    parser.add_argument("timing_json", help="Path to narration/timing.json.")
    parser.add_argument("--output-dir", default="assets/audio", help="Audio output directory.")
    parser.add_argument("--voice", default="af_heart", help="Kokoro voice name.")
    parser.add_argument("--install-hint", action="store_true", help="Print dependency install hint and exit.")
    args = parser.parse_args()

    if args.install_hint:
        print("Install Kokoro locally with: pip install kokoro soundfile")
        print("Linux may also need: sudo apt-get install espeak-ng ffmpeg")
        return

    timing_path = Path(args.timing_json).expanduser()
    beats = json.loads(timing_path.read_text(encoding="utf-8"))
    output_dir = Path(args.output_dir).expanduser()
    output_dir.mkdir(parents=True, exist_ok=True)

    kokoro_available = True
    try:
        import kokoro  # noqa: F401
        import soundfile  # noqa: F401
    except Exception as exc:
        kokoro_available = False
        print(f"Kokoro unavailable ({type(exc).__name__}: {exc}); writing silence placeholders.", file=sys.stderr)
        print("Install hint: pip install kokoro soundfile; apt-get install espeak-ng ffmpeg", file=sys.stderr)

    for beat in beats:
        audio_path = output_dir / f"{beat['beat_id']}.wav"
        if kokoro_available:
            try:
                duration = generate_with_kokoro(beat["text"], audio_path, args.voice)
            except Exception as exc:
                print(f"Kokoro failed for {beat['beat_id']} ({type(exc).__name__}: {exc}); writing silence.", file=sys.stderr)
                write_silence(audio_path, float(beat.get("duration", 2.0)))
        else:
            write_silence(audio_path, float(beat.get("duration", 2.0)))
        duration = wav_duration(audio_path)
        beat["audio"] = str(audio_path)
        beat["duration"] = round(duration, 3)

    cursor = 0.0
    for beat in beats:
        beat["start"] = round(cursor, 2)
        cursor += float(beat.get("duration", 2.0))
        beat["end"] = round(cursor, 2)

    timing_path.write_text(json.dumps(beats, indent=2) + "\n", encoding="utf-8")
    print(f"Updated {timing_path} with audio paths.")


if __name__ == "__main__":
    main()
