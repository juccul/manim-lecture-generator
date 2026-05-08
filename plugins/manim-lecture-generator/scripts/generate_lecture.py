#!/usr/bin/env python3
"""Generate a finished narrated Manim lecture video from PDFs.

This script provides a deterministic end-to-end baseline. Codex can replace the
generated plan/script/scenes with richer material, but the pipeline still works
without an external API.
"""

from __future__ import annotations

import argparse
import os
import json
import pprint
import re
import shutil
import subprocess
import sys
from pathlib import Path


PLUGIN_DIR = Path(__file__).resolve().parents[1]
REQUIRED_MODULES = ("kokoro", "soundfile", "manim")
WORD_RE = re.compile(r"[A-Za-z0-9]+(?:'[A-Za-z0-9]+)?")
SENTENCE_RE = re.compile(r"(?<=[.!?])\s+")


def run(cmd: list[str], cwd: Path | None = None, check: bool = True) -> subprocess.CompletedProcess:
    print("+", " ".join(cmd))
    return subprocess.run(cmd, cwd=str(cwd) if cwd else None, check=check, text=True)


def find_workspace_root(inputs: list[str]) -> Path:
    for raw in inputs:
        path = Path(raw).expanduser()
        if path.exists():
            return (path if path.is_dir() else path.parent).resolve()
    return Path.cwd().resolve()


def venv_python(workspace: Path) -> Path:
    if os.name == "nt":
        return workspace / ".venv" / "Scripts" / "python.exe"
    return workspace / ".venv" / "bin" / "python"


def module_available(module: str) -> bool:
    result = subprocess.run(
        [sys.executable, "-c", f"import {module}"],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return result.returncode == 0


def dependencies_available() -> bool:
    return all(module_available(module) for module in REQUIRED_MODULES)


def choose_python_for_venv() -> str:
    for candidate in ("python3.12", "python3.11", "python3", "python"):
        path = shutil.which(candidate)
        if path:
            return path
    raise SystemExit("No Python interpreter found for creating .venv.")


def ensure_venv(workspace: Path) -> Path:
    py = venv_python(workspace)
    if not py.exists():
        base_python = choose_python_for_venv()
        run([base_python, "-m", "venv", str(workspace / ".venv")])
    run([str(py), "-m", "pip", "install", "--upgrade", "pip", "setuptools", "wheel"])
    run([str(py), "-m", "pip", "install", "-r", str(PLUGIN_DIR / "requirements.txt")])
    return py


def maybe_bootstrap(argv: list[str]) -> None:
    if "--no-bootstrap" in argv:
        return
    if os.environ.get("MANIM_LECTURE_BOOTSTRAPPED") == "1":
        return
    if dependencies_available():
        return

    bootstrap_parser = argparse.ArgumentParser(add_help=False)
    bootstrap_parser.add_argument("--workspace")
    bootstrap_parser.add_argument("inputs", nargs="*")
    known, _ = bootstrap_parser.parse_known_args(argv)
    workspace = Path(known.workspace).expanduser().resolve() if known.workspace else find_workspace_root(known.inputs)
    py = ensure_venv(workspace)

    env = os.environ.copy()
    env["MANIM_LECTURE_BOOTSTRAPPED"] = "1"
    print(f"Re-running inside workspace venv: {py}")
    os.execve(str(py), [str(py), str(Path(__file__).resolve()), *argv], env)


def slugify(value: str) -> str:
    value = re.sub(r"[^a-zA-Z0-9]+", "-", value.lower()).strip("-")
    return value or "lecture"


def discover_pdfs(inputs: list[str]) -> list[Path]:
    roots = [Path(p).expanduser() for p in inputs] or [Path.cwd()]
    pdfs: list[Path] = []
    for root in roots:
        if root.is_dir():
            pdfs.extend(sorted(root.rglob("*.pdf")))
        elif root.suffix.lower() == ".pdf":
            pdfs.append(root)
    seen: set[Path] = set()
    result: list[Path] = []
    for pdf in pdfs:
        resolved = pdf.resolve()
        if resolved not in seen:
            seen.add(resolved)
            result.append(resolved)
    return result


def read_extracted_text(path: Path, max_chars: int) -> str:
    text = path.read_text(encoding="utf-8", errors="replace")
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"[ \t]+", " ", text)
    return text[:max_chars]


def choose_title(pdfs: list[Path]) -> str:
    if len(pdfs) == 1:
        return pdfs[0].stem.replace("_", " ").replace("-", " ").title()
    common = Path(pdfs[0]).stem.split("_")[0]
    return f"{common.title()} Lecture"


def summarize_to_beats(text: str, title: str, max_beats: int) -> list[dict]:
    cleaned = re.sub(r"#+\s+.*", " ", text)
    cleaned = re.sub(r">.*", " ", cleaned)
    sentences = [s.strip() for s in SENTENCE_RE.split(cleaned) if len(s.strip()) > 40]
    if not sentences:
        sentences = [
            f"This lecture introduces the main ideas from {title}.",
            "We will move from definitions to examples and then to the key structural relationships.",
            "The goal is to make the source material teachable through clear visual steps.",
        ]

    selected = sentences[: max(3, max_beats)]
    beats: list[dict] = [
        {
            "beat_id": "L01_S01_B01",
            "scene": "Lecture01",
            "kind": "title",
            "title": title,
            "text": f"Welcome. In this lecture, we will study {title}. We will build the ideas carefully and keep the notation visible as we go.",
        }
    ]
    for index, sentence in enumerate(selected[:max_beats], start=2):
        words = sentence.split()
        short = " ".join(words[:34])
        if len(words) > 34:
            short += "..."
        beats.append(
            {
                "beat_id": f"L01_S01_B{index:02d}",
                "scene": "Lecture01",
                "kind": "concept",
                "title": f"Step {index - 1}",
                "screen_text": short,
                "text": sentence,
            }
        )
    beats.append(
        {
            "beat_id": f"L01_S01_B{len(beats) + 1:02d}",
            "scene": "Lecture01",
            "kind": "recap",
            "title": "Recap",
            "text": "To recap, we identified the main definitions, connected them to examples, and prepared the ground for the next result.",
        }
    )
    return beats


def estimate_timing(beats: list[dict], wpm: float) -> list[dict]:
    cursor = 0.0
    timed = []
    for beat in beats:
        words = len(WORD_RE.findall(beat["text"]))
        duration = max(2.0, round((words / wpm) * 60.0, 2))
        timed.append(
            {
                **beat,
                "audio": None,
                "word_count": words,
                "duration": duration,
                "start": round(cursor, 2),
                "end": round(cursor + duration, 2),
            }
        )
        cursor += duration
    return timed


def write_project_files(project: Path, pdfs: list[Path], title: str, beats: list[dict]) -> None:
    (project / "notes").mkdir(parents=True, exist_ok=True)
    (project / "narration").mkdir(parents=True, exist_ok=True)
    (project / "scenes").mkdir(parents=True, exist_ok=True)
    (project / "assets" / "audio").mkdir(parents=True, exist_ok=True)
    (project / "renders").mkdir(parents=True, exist_ok=True)

    script_lines = [f"# {title} Narration\n"]
    for beat in beats:
        script_lines.append(f"### {beat['beat_id']} - {beat['scene']}\n\n{beat['text']}\n")
    (project / "narration" / "script.md").write_text("\n".join(script_lines), encoding="utf-8")
    (project / "narration" / "timing.json").write_text(json.dumps(beats, indent=2) + "\n", encoding="utf-8")
    (project / "notes" / "lecture_plan.md").write_text(build_plan(title, pdfs, beats), encoding="utf-8")
    (project / "README.md").write_text(build_readme(title), encoding="utf-8")
    scene_path = project / "scenes" / "lecture_01.py"
    write_scene(scene_path, beats)
    run([sys.executable, "-m", "py_compile", str(scene_path)])


def refresh_scene_from_timing(project: Path) -> None:
    timing_path = project / "narration" / "timing.json"
    scene_path = project / "scenes" / "lecture_01.py"
    beats = json.loads(timing_path.read_text(encoding="utf-8"))
    write_scene(scene_path, beats)
    run([sys.executable, "-m", "py_compile", str(scene_path)])


def build_plan(title: str, pdfs: list[Path], beats: list[dict]) -> str:
    pdf_list = "\n".join(f"- {pdf}" for pdf in pdfs)
    beat_list = "\n".join(f"- `{beat['beat_id']}`: {beat['title']}" for beat in beats)
    return f"""# {title}

## Sources

{pdf_list}

## Lecture Structure

{beat_list}

## Notes

This baseline plan was generated automatically. Codex should deepen it by adding
page-referenced definitions, theorems, proofs, examples, and exercises from the
extracted source notes before producing a final course-quality lecture.
"""


def build_readme(title: str) -> str:
    return f"""# {title}

## Render

```bash
manim -ql scenes/lecture_01.py Lecture01
```

## TTS

The plugin defaults to local Kokoro TTS when installed. If Kokoro is unavailable,
the project keeps estimated timings so the visual render can still be validated.

## Layout

The generated scene uses bounded safe zones and scales each text group to fit
inside the frame. Run `validate_manim_layout.py` after render to inspect output
frames for blank or cropped videos.
"""


def py_string(value: str) -> str:
    return repr(value)


def write_scene(path: Path, beats: list[dict]) -> None:
    payload = pprint.pformat(beats, width=100, sort_dicts=False)
    path.write_text(
        f'''from __future__ import annotations

from manim import *


BEATS = {payload}

FRAME_W = 13.6
FRAME_H = 7.65
SAFE_W = 12.2
SAFE_H = 6.6


def fit_to_safe_zone(mobject: Mobject, max_width: float = SAFE_W, max_height: float = SAFE_H) -> Mobject:
    if mobject.width > max_width:
        mobject.scale_to_fit_width(max_width)
    if mobject.height > max_height:
        mobject.scale_to_fit_height(max_height)
    return mobject


def safe_text(text: str, font_size: int = 34, width: float = 11.4) -> Paragraph:
    words = text.split()
    lines = []
    line = []
    count = 0
    for word in words:
        if count + len(word) + 1 > 58:
            lines.append(" ".join(line))
            line = [word]
            count = len(word)
        else:
            line.append(word)
            count += len(word) + 1
    if line:
        lines.append(" ".join(line))
    paragraph = Paragraph(*lines[:5], alignment="center", font_size=font_size, line_spacing=0.55)
    return fit_to_safe_zone(paragraph, max_width=width, max_height=4.4)


class Lecture01(Scene):
    def construct(self):
        self.camera.background_color = "#101820"
        for beat in BEATS:
            audio = beat.get("audio")
            if audio:
                self.add_sound(audio)

            title = Text(beat.get("title", "Lecture"), font_size=36, weight=BOLD, color=BLUE_B)
            title.to_edge(UP, buff=0.38)
            fit_to_safe_zone(title, max_width=11.8, max_height=0.7)

            if beat.get("kind") == "title":
                body = safe_text(beat["text"], font_size=32, width=11.0)
            elif beat.get("kind") == "recap":
                body = VGroup(
                    safe_text(beat["text"], font_size=30, width=10.8),
                    MathTex(r"\\text{{definitions}} \\rightarrow \\text{{examples}} \\rightarrow \\text{{structure}}", font_size=36),
                ).arrange(DOWN, buff=0.45)
                fit_to_safe_zone(body, max_width=11.3, max_height=4.8)
            else:
                body = VGroup(
                    safe_text(beat.get("screen_text") or beat["text"], font_size=30, width=10.8),
                    MathTex(r"\\text{{idea}} \\quad \\Longrightarrow \\quad \\text{{example}}", font_size=38),
                ).arrange(DOWN, buff=0.45)
                fit_to_safe_zone(body, max_width=11.3, max_height=4.8)

            body.move_to(ORIGIN)
            footer = Text(beat["beat_id"], font_size=18, color=GRAY_B).to_edge(DOWN, buff=0.3)
            group = VGroup(title, body, footer)
            fit_to_safe_zone(group, max_width=12.4, max_height=7.0)

            duration = max(0.2, float(beat.get("duration", 3.0)))
            fade_in = min(0.35, duration * 0.15)
            fade_out = min(0.25, duration * 0.1)
            hold = max(0.01, duration - fade_in - fade_out)

            self.play(FadeIn(title, shift=DOWN * 0.1), FadeIn(body, shift=UP * 0.1), run_time=fade_in)
            self.wait(hold)
            self.play(FadeOut(title), FadeOut(body), run_time=fade_out)
            self.remove(title, body, footer)
''',
        encoding="utf-8",
    )


def generate_tts(project: Path, voice: str) -> None:
    script = PLUGIN_DIR / "scripts" / "tts_kokoro.py"
    timing = project / "narration" / "timing.json"
    run(
        [
            sys.executable,
            str(script),
            str(timing),
            "--output-dir",
            str(project / "assets" / "audio"),
            "--voice",
            voice,
        ],
        check=False,
    )
    refresh_scene_from_timing(project)


def render_manim(project: Path, quality: str) -> bool:
    try:
        import manim  # noqa: F401
    except Exception:
        manim = None

    if manim is not None:
        result = run([sys.executable, "-m", "manim", quality, "scenes/lecture_01.py", "Lecture01"], cwd=project, check=False)
        return result.returncode == 0

    if shutil.which("manim") is None:
        print("Manim is not installed in this Python environment or on PATH; skipping render.", file=sys.stderr)
        return False
    result = run(["manim", quality, "scenes/lecture_01.py", "Lecture01"], cwd=project, check=False)
    return result.returncode == 0


def mux_audio(project: Path) -> None:
    script = PLUGIN_DIR / "scripts" / "mux_narration.py"
    run([sys.executable, str(script), str(project)], check=False)


def main() -> None:
    maybe_bootstrap(sys.argv[1:])
    parser = argparse.ArgumentParser(description="Generate an end-to-end Manim lecture video from PDFs.")
    parser.add_argument("inputs", nargs="*", help="PDF files or directories. Defaults to cwd.")
    parser.add_argument("--out", help="Output project directory.")
    parser.add_argument("--workspace", help="Workspace where .venv should be created or reused.")
    parser.add_argument("--title", help="Lecture title.")
    parser.add_argument("--max-chars", type=int, default=18000, help="Maximum extracted text to plan from.")
    parser.add_argument("--max-beats", type=int, default=8, help="Maximum source-derived narration beats.")
    parser.add_argument("--wpm", type=float, default=145.0, help="Timing estimate words per minute.")
    parser.add_argument("--voice", default="af_heart", help="Kokoro voice name.")
    parser.add_argument("--quality", default="-ql", help="Manim quality flag.")
    parser.add_argument("--no-tts", action="store_true", help="Skip local TTS generation.")
    parser.add_argument("--no-render", action="store_true", help="Skip Manim render.")
    parser.add_argument("--no-bootstrap", action="store_true", help="Do not auto-create or use a workspace .venv.")
    args = parser.parse_args()

    pdfs = discover_pdfs(args.inputs)
    if not pdfs:
        raise SystemExit("No PDFs found. Pass PDF files or a directory containing PDFs.")

    title = args.title or choose_title(pdfs)
    project = Path(args.out).expanduser().resolve() if args.out else Path.cwd() / "manim_lectures" / slugify(title)
    project.mkdir(parents=True, exist_ok=True)

    run([sys.executable, str(PLUGIN_DIR / "scripts" / "pdf_inventory.py"), *map(str, pdfs), "--output", str(project / "source_index.json")])
    run([sys.executable, str(PLUGIN_DIR / "scripts" / "extract_pdf_text.py"), *map(str, pdfs), "--output", str(project / "notes" / "extracted.md")])

    text = read_extracted_text(project / "notes" / "extracted.md", args.max_chars)
    beats = estimate_timing(summarize_to_beats(text, title, args.max_beats), args.wpm)
    write_project_files(project, pdfs, title, beats)

    if not args.no_tts:
        generate_tts(project, args.voice)
    if not args.no_render:
        rendered = render_manim(project, args.quality)
        if rendered:
            mux_audio(project)
            run([sys.executable, str(PLUGIN_DIR / "scripts" / "validate_manim_layout.py"), str(project)], check=False)

    print(f"Project ready: {project}")


if __name__ == "__main__":
    main()
