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


def infer_visual_kind(title: str, text: str) -> str:
    haystack = f"{title} {text}".lower()
    if any(word in haystack for word in ("modular", "modulo", "congruence", "residue")):
        return "modular_clock"
    if any(word in haystack for word in ("symmetry", "rotation", "reflection", "square", "dihedral")):
        return "symmetry_square"
    if any(word in haystack for word in ("equivalence", "class", "partition", "representative")):
        return "equivalence_partition"
    if any(word in haystack for word in ("closure", "well-defined", "operation on a set")):
        return "closure_map"
    if any(word in haystack for word in ("operation", "binary", "unary", "arity", "composition")):
        return "operation_machine"
    if any(word in haystack for word in ("group", "identity", "inverse", "associativity", "axiom")):
        return "group_axioms"
    if any(word in haystack for word in ("function", "graph", "curve", "polynomial")):
        return "coordinate_graph"
    return "concept_map"


def math_statement_for_visual(visual: str) -> str:
    statements = {
        "modular_clock": r"a \equiv b \pmod n \iff n \mid (a-b)",
        "symmetry_square": r"D_4=\{e,r,r^2,r^3,s,sr,sr^2,sr^3\}",
        "equivalence_partition": r"[a]=\{x\in S: x\sim a\}",
        "closure_map": r"\star:S\times S\to S",
        "operation_machine": r"(a,b)\mapsto a\star b",
        "group_axioms": r"(G,\star):\ e,\ a^{-1},\ (a\star b)\star c=a\star(b\star c)",
        "coordinate_graph": r"f:X\to Y",
        "concept_map": r"\text{objects}+\text{operations}+\text{laws}",
    }
    return statements.get(visual, statements["concept_map"])


def sentence_score(sentence: str) -> int:
    lower = sentence.lower()
    score = sum(3 for word in MATH_KEYWORDS if word in lower)
    score += min(len(WORD_RE.findall(sentence)) // 12, 4)
    score -= sum(4 for word in ADMIN_KEYWORDS if word in lower)
    if any(symbol in sentence for symbol in ("≡", "∼", "→", "\\", "∈", "∀", "=")):
        score += 2
    return score


def title_for_sentence(sentence: str, visual: str, index: int) -> str:
    lower = sentence.lower()
    if visual == "modular_clock":
        if "well-defined" in lower:
            return "Well-Defined Modular Arithmetic"
        return "Modular Congruence"
    if visual == "symmetry_square":
        return "Symmetries As Operations"
    if visual == "equivalence_partition":
        return "Equivalence Classes"
    if visual == "closure_map":
        return "Closure"
    if visual == "operation_machine":
        return "Operations And Arity"
    if visual == "group_axioms":
        return "Group Axiom Check"
    if visual == "coordinate_graph":
        return "Functions As Structure"
    return f"Mathematical Idea {index}"


def detect_topics(text: str) -> list[str]:
    lower = text.lower()
    topics: list[str] = []
    checks = [
        ("operation_machine", ("operation", "binary", "unary", "arity", "composition", "merging")),
        ("closure_map", ("closure", "well-defined", "same set")),
        ("equivalence_partition", ("equivalence", "partition", "class", "representative", "reflexive")),
        ("modular_clock", ("modular", "modulo", "congruence", "remainder", "residue")),
        ("symmetry_square", ("symmetry", "rotation", "reflection", "square")),
        ("group_axioms", ("group", "identity", "inverse", "associative", "associativity")),
    ]
    for topic, words in checks:
        if any(word in lower for word in words):
            topics.append(topic)
    return topics or ["operation_machine", "equivalence_partition", "modular_clock", "symmetry_square", "group_axioms"]


def authored_beat(beat_id: str, visual: str) -> dict:
    lessons = {
        "operation_machine": {
            "title": "A Structure Is More Than A Set",
            "onscreen": "same objects, new rule",
            "math": r"(S,\star)",
            "text": (
                "Start with a set of objects. By itself, the set is just a collection. "
                "Algebra begins when we choose a rule for combining or transforming those objects. "
                "Changing the rule can completely change the structure, even when the underlying set stays the same."
            ),
        },
        "closure_map": {
            "title": "Closure Is The First Test",
            "onscreen": "the result must stay inside",
            "math": r"\star:S\times S\to S",
            "text": (
                "The first thing to check is closure. If two inputs come from the set, the operation has to produce another element of that same set. "
                "Without closure, the rule may still be useful, but it is not an operation on that set."
            ),
        },
        "equivalence_partition": {
            "title": "Equivalence Classes Group Objects",
            "onscreen": "replace equality by a chosen notion of sameness",
            "math": r"[a]=\{x\in S:x\sim a\}",
            "text": (
                "An equivalence relation lets us decide when two objects should count as the same for the problem at hand. "
                "Each object then belongs to a class of interchangeable representatives, and those classes carve the whole set into non-overlapping regions."
            ),
        },
        "modular_clock": {
            "title": "Modulo Arithmetic Wraps The Number Line",
            "onscreen": "keep the remainder, forget the lap",
            "math": r"a\equiv b\pmod n\iff n\mid(a-b)",
            "text": (
                "Modulo arithmetic turns the infinite number line into a clock. "
                "Numbers that land at the same clock position are treated as equivalent, because their difference is a whole number of laps around the clock."
            ),
        },
        "symmetry_square": {
            "title": "Symmetries Form A Calculus Of Motion",
            "onscreen": "compose motions, not numbers",
            "math": r"\rho\circ\sigma",
            "text": (
                "A symmetry is a motion that leaves the relevant shape unchanged. "
                "If we do one symmetry and then another, the result is again a symmetry. "
                "That makes composition the operation, and the shape becomes a concrete model of algebra."
            ),
        },
        "group_axioms": {
            "title": "Groups Package The Repeating Pattern",
            "onscreen": "identity, inverse, associativity",
            "math": r"e,\quad a^{-1},\quad (ab)c=a(bc)",
            "text": (
                "Groups isolate a pattern that appears in arithmetic and in symmetry. "
                "There is a do-nothing move, every move can be undone, and parentheses do not change the result of repeated composition. "
                "The point of the definition is to make these different examples speak the same language."
            ),
        },
    }
    beat = lessons[visual]
    return {
        "beat_id": beat_id,
        "scene": "Lecture01",
        "kind": "concept",
        "visual": visual,
        **beat,
    }


def summarize_to_beats(text: str, title: str, max_beats: int) -> list[dict]:
    topics = detect_topics(text)[:max_beats]
    beats: list[dict] = [
        {
            "beat_id": "L01_S01_B01",
            "scene": "Lecture01",
            "kind": "title",
            "title": title,
            "visual": "concept_map",
            "onscreen": "objects, operations, laws",
            "math": r"\text{objects}+\text{operations}+\text{laws}",
            "text": (
                f"This lecture is about the central move in algebra: taking familiar objects and asking what structure appears when we choose an operation. "
                "We will use the source material as our guide, but the goal here is to build intuition through pictures, examples, and a few precise formulas."
            ),
        }
    ]
    for index, visual in enumerate(topics, start=2):
        beats.append(authored_beat(f"L01_S01_B{index:02d}", visual))
    beats.append(
        {
            "beat_id": f"L01_S01_B{len(beats) + 1:02d}",
            "scene": "Lecture01",
            "kind": "recap",
            "title": "Recap",
            "visual": "group_axioms",
            "onscreen": "look for the structure",
            "math": r"\text{definition}\to\text{example}\to\text{axiom check}",
            "text": (
                "The habit to take away is simple. First identify the objects, then identify the operation, and then check the laws the structure claims to satisfy. "
                "That workflow turns abstract definitions into something you can test on concrete examples."
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

import numpy as np
from manim import *


BEATS = {payload}

FRAME_W = 13.6
FRAME_H = 7.65
SAFE_W = 12.2
SAFE_H = 6.6
TITLE_COLOR = "#F2C14E"
ACCENT = "#4FB3BF"
SECONDARY = "#F78154"
INK = "#F4F1DE"
MUTED = "#9AA0A6"


def fit_to_safe_zone(mobject: Mobject, max_width: float = SAFE_W, max_height: float = SAFE_H) -> Mobject:
    if mobject.width > max_width:
        mobject.scale_to_fit_width(max_width)
    if mobject.height > max_height:
        mobject.scale_to_fit_height(max_height)
    return mobject


def safe_text(text: str, font_size: int = 28, width: float = 5.7, max_lines: int = 5) -> Paragraph:
    words = text.split()
    lines = []
    line = []
    count = 0
    for word in words:
        if count + len(word) + 1 > 42:
            lines.append(" ".join(line))
            line = [word]
            count = len(word)
        else:
            line.append(word)
            count += len(word) + 1
    if line:
        lines.append(" ".join(line))
    paragraph = Paragraph(*lines[:max_lines], alignment="left", font_size=font_size, line_spacing=0.58)
    return fit_to_safe_zone(paragraph, max_width=width, max_height=3.2)


def math_label(tex: str, font_size: int = 36, color=INK) -> MathTex:
    item = MathTex(tex, font_size=font_size, color=color)
    return fit_to_safe_zone(item, max_width=5.9, max_height=1.15)


def concept_map_visual(beat: dict) -> VGroup:
    labels = ["Objects", "Operations", "Laws"]
    nodes = VGroup(*[
        RoundedRectangle(width=2.25, height=0.8, corner_radius=0.12, color=ACCENT).set_fill("#172A3A", 0.75)
        for _ in labels
    ]).arrange(RIGHT, buff=0.55)
    text = VGroup(*[Text(label, font_size=24, color=INK).move_to(node) for label, node in zip(labels, nodes)])
    arrows = VGroup(*[Arrow(nodes[i].get_right(), nodes[i + 1].get_left(), buff=0.12, color=SECONDARY) for i in range(2)])
    formula = math_label(beat.get("math", r"\\text{{structure}}"), 34).next_to(nodes, DOWN, buff=0.55)
    return VGroup(nodes, text, arrows, formula)


def modular_clock_visual(beat: dict) -> VGroup:
    circle = Circle(radius=1.45, color=ACCENT, stroke_width=4)
    ticks = VGroup()
    labels = VGroup()
    for k in range(5):
        angle = PI / 2 - TAU * k / 5
        point = circle.get_center() + 1.45 * (RIGHT * np.cos(angle) + UP * np.sin(angle))
        ticks.add(Dot(point, radius=0.055, color=SECONDARY))
        labels.add(Text(str(k), font_size=22, color=INK).move_to(circle.get_center() + 1.78 * (RIGHT * np.cos(angle) + UP * np.sin(angle))))
    arrow = CurvedArrow(labels[2].get_center(), labels[0].get_center(), radius=-2.2, color=SECONDARY)
    calc = MathTex(r"12+18\\equiv 2+3\\equiv 0\\pmod 5", font_size=34, color=INK)
    calc.next_to(circle, DOWN, buff=0.42)
    return VGroup(circle, ticks, labels, arrow, calc)


def symmetry_square_visual(beat: dict) -> VGroup:
    square = Square(side_length=2.2, color=ACCENT, stroke_width=5)
    vertices = VGroup(*[Dot(square.get_vertices()[i], color=SECONDARY) for i in range(4)])
    labels = VGroup(*[Text(str(i + 1), font_size=20, color=INK).next_to(vertices[i], square.get_vertices()[i] - square.get_center(), buff=0.12) for i in range(4)])
    rot = CurvedArrow(UP * 1.45 + RIGHT * 0.25, RIGHT * 1.45 + DOWN * 0.25, radius=-1.5, color=SECONDARY)
    mirror = DashedLine(UP * 1.55, DOWN * 1.55, color=MUTED)
    formula = math_label(beat.get("math", r"D_4"), 31).next_to(square, DOWN, buff=0.45)
    return VGroup(square, vertices, labels, rot, mirror, formula)


def equivalence_partition_visual(beat: dict) -> VGroup:
    colors = [ACCENT, SECONDARY, "#90BE6D"]
    classes = VGroup()
    for i, color in enumerate(colors):
        blob = Ellipse(width=1.55, height=2.35, color=color, stroke_width=3).set_fill(color, 0.16)
        dots = VGroup(*[Dot(radius=0.055, color=INK).shift(RIGHT * ((j % 2) - 0.5) * 0.48 + UP * (j - 1) * 0.35) for j in range(3)])
        dots.move_to(blob)
        label = MathTex(rf"[{{i}}]", font_size=28, color=color).next_to(blob, DOWN, buff=0.14)
        classes.add(VGroup(blob, dots, label))
    classes.arrange(RIGHT, buff=0.35)
    formula = math_label(beat.get("math", r"[a]"), 32).next_to(classes, DOWN, buff=0.42)
    return VGroup(classes, formula)


def closure_map_visual(beat: dict) -> VGroup:
    domain = RoundedRectangle(width=3.0, height=2.35, corner_radius=0.2, color=ACCENT).set_fill("#172A3A", 0.55)
    a = Dot(domain.get_center() + LEFT * 0.65 + UP * 0.35, color=INK)
    b = Dot(domain.get_center() + RIGHT * 0.15 + DOWN * 0.25, color=INK)
    c = Dot(domain.get_center() + RIGHT * 0.75 + UP * 0.05, color=SECONDARY)
    arrow1 = Arrow(a.get_center(), c.get_center(), buff=0.12, color=SECONDARY)
    arrow2 = Arrow(b.get_center(), c.get_center(), buff=0.12, color=SECONDARY)
    label = MathTex(r"a\\star b\\in S", font_size=34, color=INK).next_to(domain, DOWN, buff=0.42)
    return VGroup(domain, a, b, c, arrow1, arrow2, label)


def operation_machine_visual(beat: dict) -> VGroup:
    left = VGroup(MathTex("a", color=INK), MathTex("b", color=INK)).arrange(DOWN, buff=0.45)
    box = RoundedRectangle(width=2.15, height=1.25, corner_radius=0.18, color=ACCENT).set_fill("#172A3A", 0.8)
    op = MathTex(r"\\star", font_size=46, color=SECONDARY).move_to(box)
    out = MathTex(r"a\\star b", font_size=34, color=INK)
    group = VGroup(left, VGroup(box, op), out).arrange(RIGHT, buff=0.6)
    arrows = VGroup(Arrow(left.get_right(), box.get_left(), buff=0.1, color=MUTED), Arrow(box.get_right(), out.get_left(), buff=0.1, color=MUTED))
    formula = math_label(beat.get("math", r"(a,b)\\mapsto a\\star b"), 32).next_to(group, DOWN, buff=0.45)
    return VGroup(group, arrows, formula)


def group_axioms_visual(beat: dict) -> VGroup:
    formulas = VGroup(
        MathTex(r"e\\star a=a", font_size=31, color=INK),
        MathTex(r"a\\star a^{-1}=e", font_size=31, color=INK),
        MathTex(r"(a\\star b)\\star c=a\\star(b\\star c)", font_size=31, color=INK),
    ).arrange(DOWN, aligned_edge=LEFT, buff=0.28)
    brace = Brace(formulas, LEFT, color=SECONDARY)
    label = Text("axiom checks", font_size=22, color=SECONDARY).next_to(brace, LEFT, buff=0.18)
    return VGroup(formulas, brace, label)


def coordinate_graph_visual(beat: dict) -> VGroup:
    axes = Axes(x_range=[-3, 3, 1], y_range=[-1, 5, 1], x_length=4.6, y_length=3.1, tips=False, axis_config={{"color": MUTED}})
    curve = axes.plot(lambda x: 0.45 * (x - 0.4) ** 2 + 0.25, x_range=[-2.6, 2.7], color=SECONDARY)
    label = MathTex(r"f(x)", font_size=30, color=INK).next_to(axes, UP, buff=0.1)
    return VGroup(axes, curve, label)


def visual_for(beat: dict) -> VGroup:
    visual = beat.get("visual", "concept_map")
    builders = {{
        "modular_clock": modular_clock_visual,
        "symmetry_square": symmetry_square_visual,
        "equivalence_partition": equivalence_partition_visual,
        "closure_map": closure_map_visual,
        "operation_machine": operation_machine_visual,
        "group_axioms": group_axioms_visual,
        "coordinate_graph": coordinate_graph_visual,
        "concept_map": concept_map_visual,
    }}
    item = builders.get(visual, concept_map_visual)(beat)
    return fit_to_safe_zone(item, max_width=5.9, max_height=4.15)


def beat_panel(beat: dict) -> VGroup:
    prose = safe_text(beat.get("onscreen") or beat.get("title", ""), font_size=28, width=5.4, max_lines=3)
    formula = math_label(beat.get("math", r"\\text{{idea}}"), 32)
    panel = VGroup(formula, prose).arrange(DOWN, aligned_edge=LEFT, buff=0.35)
    return fit_to_safe_zone(panel, max_width=5.8, max_height=4.4)


class Lecture01(Scene):
    def construct(self):
        self.camera.background_color = "#101418"
        for beat in BEATS:
            audio = beat.get("audio")
            if audio:
                self.add_sound(audio)

            title = Text(beat.get("title", "Lecture"), font_size=34, weight=BOLD, color=TITLE_COLOR)
            title.to_edge(UP, buff=0.32)
            fit_to_safe_zone(title, max_width=11.8, max_height=0.7)

            visual = visual_for(beat)
            panel = beat_panel(beat)
            body = VGroup(visual, panel).arrange(RIGHT, buff=0.72)
            body.move_to(ORIGIN + DOWN * 0.08)
            fit_to_safe_zone(body, max_width=12.0, max_height=4.9)

            footer = Text(beat["beat_id"], font_size=18, color=GRAY_B).to_edge(DOWN, buff=0.3)
            group = VGroup(title, body, footer)
            fit_to_safe_zone(group, max_width=12.4, max_height=7.0)

            duration = max(0.2, float(beat.get("duration", 3.0)))
            fade_in = min(0.35, duration * 0.15)
            fade_out = min(0.25, duration * 0.1)
            hold = max(0.01, duration - fade_in - fade_out)

            self.play(FadeIn(title, shift=DOWN * 0.1), FadeIn(visual, shift=RIGHT * 0.12), FadeIn(panel, shift=LEFT * 0.12), run_time=fade_in)
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
