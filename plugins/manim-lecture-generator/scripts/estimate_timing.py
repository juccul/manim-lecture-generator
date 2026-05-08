#!/usr/bin/env python3
"""Estimate narration beat timings from a Markdown script."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path


BEAT_RE = re.compile(r"^###\s+(?P<beat_id>[A-Za-z0-9_-]+)(?:\s+-\s+(?P<scene>.+))?\s*$")
WORD_RE = re.compile(r"[A-Za-z0-9]+(?:'[A-Za-z0-9]+)?")


def parse_beats(text: str) -> list[dict]:
    beats: list[dict] = []
    current: dict | None = None
    lines: list[str] = []

    for line in text.splitlines():
        match = BEAT_RE.match(line)
        if match:
            if current is not None:
                current["text"] = "\n".join(lines).strip()
                beats.append(current)
            current = {
                "beat_id": match.group("beat_id"),
                "scene": (match.group("scene") or "").strip() or None,
            }
            lines = []
        elif current is not None:
            lines.append(line)

    if current is not None:
        current["text"] = "\n".join(lines).strip()
        beats.append(current)

    return beats


def main() -> None:
    parser = argparse.ArgumentParser(description="Estimate TTS beat timings.")
    parser.add_argument("script", help="Markdown narration script with ### BEAT_ID headings.")
    parser.add_argument("--wpm", type=float, default=145.0, help="Words per minute.")
    parser.add_argument("--output", default="narration/timing.json", help="Output JSON path.")
    args = parser.parse_args()

    script_path = Path(args.script).expanduser()
    beats = parse_beats(script_path.read_text(encoding="utf-8"))

    cursor = 0.0
    timed = []
    for beat in beats:
        word_count = len(WORD_RE.findall(beat["text"]))
        duration = max(1.0, round((word_count / args.wpm) * 60.0, 2))
        item = {
            **beat,
            "audio": None,
            "word_count": word_count,
            "duration": duration,
            "start": round(cursor, 2),
            "end": round(cursor + duration, 2),
        }
        timed.append(item)
        cursor += duration

    output = Path(args.output).expanduser()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(timed, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {output} with {len(timed)} beat(s).")


if __name__ == "__main__":
    main()

