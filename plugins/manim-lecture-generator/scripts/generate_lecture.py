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
MATH_KEYWORDS = (
    "operation",
    "structure",
    "modular",
    "modulo",
    "congruence",
    "equivalence",
    "class",
    "partition",
    "closure",
    "symmetry",
    "rotation",
    "reflection",
    "group",
    "identity",
    "inverse",
    "associativity",
    "function",
    "theorem",
    "proof",
    "axiom",
)
ADMIN_KEYWORDS = ("canvas", "tutorial", "lectures:", "module manual", "reader:", "q3", "welcome")


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


def sentence_score(sentence: str) -> int:
    lower = sentence.lower()
    score = sum(3 for word in MATH_KEYWORDS if word in lower)
    score += min(len(WORD_RE.findall(sentence)) // 12, 4)
    score -= sum(4 for word in ADMIN_KEYWORDS if word in lower)
    if any(symbol in sentence for symbol in ("≡", "∼", "→", "\\", "∈", "∀", "=")):
        score += 2
    return score


def clean_source_excerpt(sentence: str) -> str:
    sentence = re.sub(r"Algebra\s+•.*", "", sentence)
    sentence = re.sub(r"\s+", " ", sentence).strip()
    return sentence[:420]


def selected_source_beats(text: str, max_beats: int) -> list[str]:
    cleaned = re.sub(r"#+\s+.*", " ", text)
    cleaned = re.sub(r">.*", " ", cleaned)
    sentences = [clean_source_excerpt(s) for s in SENTENCE_RE.split(cleaned) if len(s.strip()) > 40]
    sentences = [s for s in sentences if s]
    if not sentences:
        return []
    scored = [(sentence_score(sentence), position, sentence) for position, sentence in enumerate(sentences)]
    relevant = [(score, position, sentence) for score, position, sentence in scored if score > 0]
    pool = relevant or scored
    selected_ranked = sorted(pool, key=lambda item: (-item[0], item[1]))[:max(1, max_beats)]
    return [sentence for _, _, sentence in sorted(selected_ranked, key=lambda item: item[1])]


def summarize_to_beats(text: str, title: str, max_beats: int) -> list[dict]:
    source_beats = selected_source_beats(text, max_beats)
    beats: list[dict] = [
        {
            "beat_id": "L01_S01_B01",
            "scene": "Lecture01",
            "kind": "title",
            "title": title,
            "source_excerpt": "",
            "scene_brief": "Open from a blank black canvas. Invent the first visual metaphor from the lecture's actual source material.",
            "onscreen": "",
            "math": "",
            "text": (
                f"We will build a visual explanation of {title}. The PDF is only our reference: the animation should introduce the ideas in a fresh sequence, using custom visuals chosen for this material."
            ),
        }
    ]
    for index, excerpt in enumerate(source_beats, start=2):
        beats.append(
            {
                "beat_id": f"L01_S01_B{index:02d}",
                "scene": "Lecture01",
                "kind": "source_brief",
                "title": f"Source Idea {index - 1}",
                "source_excerpt": excerpt,
                "scene_brief": (
                    "Author a unique visual treatment from this source idea. Do not select from presets; decide the objects, transformations, colors, camera movement, and proof rhythm from scratch."
                ),
                "onscreen": "",
                "math": "",
                "text": (
                    "This beat must be rewritten by Codex into natural narration after interpreting the source excerpt. "
                    "Do not read the excerpt aloud; use it only to decide what should be taught."
                ),
            }
        )
    beats.append(
        {
            "beat_id": f"L01_S01_B{len(beats) + 1:02d}",
            "scene": "Lecture01",
            "kind": "recap",
            "title": "Recap",
            "source_excerpt": "",
            "scene_brief": "Close with a custom synthesis of the lecture's visual language. Reuse motifs invented earlier only if they emerged from the source.",
            "onscreen": "",
            "math": "",
            "text": (
                "End by connecting the visual ideas into one coherent takeaway. This narration should be authored after the custom scene sequence exists."
            ),
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

## Authoring Status

This project is a blank-canvas authoring scaffold. The extracted PDF text is
source context, not a visual template and not a narration script. Codex should
replace `scenes/lecture_01.py`, `narration/script.md`, and
`narration/timing.json` with a custom mathematical explanation before producing
final TTS or a finished render.

## Render

```bash
manim -ql scenes/lecture_01.py Lecture01
```

## TTS

The plugin defaults to local Kokoro TTS when installed. If Kokoro is unavailable,
the project keeps estimated timings so the visual render can still be validated.

## Layout

The scaffold intentionally avoids preset visuals. Final scenes should start from
a blank black canvas and introduce only visuals designed for the source material.
Run `validate_manim_layout.py` after render to inspect output frames for blank
or cropped videos.
"""


def py_string(value: str) -> str:
    return repr(value)


def write_scene(path: Path, beats: list[dict]) -> None:
    payload = pprint.pformat(beats, width=100, sort_dicts=False)
    scene_source = 'from __future__ import annotations\n\nfrom manim import *\n\n\nBEATS = {payload}\nBLACK = "#000000"\nINK = "#F8F8F2"\nACCENT = "#58C4DD"\nMUTED = "#6B7280"\n\n\nclass Lecture01(MovingCameraScene):\n    """Blank-canvas scaffold for custom lecture authoring.\n\n    This file is intentionally not a visual-template generator. The PDF-derived\n    beats below are source briefs only. Codex should replace this scaffold with\n    bespoke Manim objects, transformations, camera movement, and narration timing\n    chosen for the actual mathematics in the source material.\n    """\n\n    def construct(self):\n        self.camera.background_color = BLACK\n        opening = Text("Blank canvas", font_size=42, color=INK)\n        subtitle = Text("Author custom visuals from the PDF source", font_size=24, color=MUTED)\n        group = VGroup(opening, subtitle).arrange(DOWN, buff=0.28)\n        self.play(FadeIn(opening, shift=UP * 0.15), run_time=0.8)\n        self.play(FadeIn(subtitle, shift=UP * 0.1), run_time=0.6)\n        self.wait(0.8)\n        self.play(FadeOut(group), run_time=0.5)\n\n        for beat in BEATS:\n            audio = beat.get("audio")\n            if audio:\n                self.add_sound(audio)\n            title = Text(beat.get("title", "Scene brief"), font_size=30, color=ACCENT)\n            title.to_corner(UL, buff=0.45)\n            brief = Paragraph(\n                beat.get("scene_brief", "Author this scene from scratch."),\n                alignment="left",\n                font_size=22,\n                color=INK,\n                line_spacing=0.62,\n            )\n            if brief.width > 10.5:\n                brief.scale_to_fit_width(10.5)\n            if brief.height > 3.2:\n                brief.scale_to_fit_height(3.2)\n            brief.move_to(ORIGIN)\n            source_note = Text("source brief only", font_size=18, color=MUTED).to_edge(DOWN, buff=0.38)\n            self.play(FadeIn(title, shift=RIGHT * 0.15), FadeIn(brief, shift=UP * 0.12), run_time=0.7)\n            self.play(Indicate(title, color=ACCENT), run_time=0.8)\n            self.add(source_note)\n            self.wait(max(0.5, min(2.0, float(beat.get("duration", 3.0)) * 0.25)))\n            self.play(FadeOut(title), FadeOut(brief), FadeOut(source_note), run_time=0.5)\n'.format(payload=payload)
    path.write_text(scene_source, encoding="utf-8")

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


def requires_custom_authoring(beats: list[dict]) -> bool:
    return any(beat.get("kind") == "source_brief" for beat in beats)


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
    parser.add_argument("--render-scaffold", action="store_true", help="Render the blank-canvas scaffold before custom authoring.")
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

    if requires_custom_authoring(beats) and not args.render_scaffold:
        print("Project scaffold ready for custom Codex authoring; skipping TTS/render for placeholder scene briefs.")
        print(f"Project ready: {project}")
        return

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
