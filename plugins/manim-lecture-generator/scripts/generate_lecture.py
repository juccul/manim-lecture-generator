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
TITLE_COLOR = "#F7C948"
ACCENT = "#58C4DD"
SECONDARY = "#FF6B6B"
GREEN = "#7BD88F"
PURPLE = "#A78BFA"
INK = "#F8F8F2"
MUTED = "#6B7280"
BLACK = "#000000"


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


def glow(mobject: Mobject, color=ACCENT, layers: int = 4) -> VGroup:
    result = VGroup()
    for index in range(layers, 0, -1):
        copy = mobject.copy()
        copy.set_stroke(color, width=2 + 3 * index, opacity=0.06 * index)
        copy.set_fill(opacity=0)
        result.add(copy)
    return result


def ambient_field() -> VGroup:
    dots = VGroup()
    for i in range(42):
        x = -6.4 + (i * 1.73) % 12.8
        y = -3.45 + (i * 2.31) % 6.9
        radius = 0.012 + 0.01 * (i % 3)
        dot = Dot([x, y, 0], radius=radius, color=ACCENT if i % 2 else MUTED).set_opacity(0.25)
        dots.add(dot)
    return dots


def concept_map_visual(beat: dict) -> VGroup:
    labels = ["Objects", "Operations", "Laws"]
    angles = [PI * 0.9, PI * 0.1, -PI / 2]
    nodes = VGroup()
    text = VGroup()
    for label, angle, color in zip(labels, angles, [ACCENT, SECONDARY, GREEN]):
        point = 2.05 * (RIGHT * np.cos(angle) + UP * np.sin(angle))
        circle = Circle(radius=0.62, color=color, stroke_width=4).move_to(point)
        nodes.add(VGroup(glow(circle, color, 3), circle))
        text.add(Text(label, font_size=22, color=INK).move_to(point))
    connectors = VGroup(*[
        Line(nodes[i].get_center(), nodes[(i + 1) % 3].get_center(), color=MUTED, stroke_width=2).set_opacity(0.75)
        for i in range(3)
    ])
    formula = math_label(beat.get("math", r"\\text{{structure}}"), 34).move_to(ORIGIN)
    return VGroup(connectors, nodes, text, formula)


def modular_clock_visual(beat: dict) -> VGroup:
    circle = Circle(radius=1.75, color=ACCENT, stroke_width=5)
    ticks = VGroup()
    labels = VGroup()
    for k in range(5):
        angle = PI / 2 - TAU * k / 5
        point = circle.get_center() + 1.75 * (RIGHT * np.cos(angle) + UP * np.sin(angle))
        ticks.add(Dot(point, radius=0.075, color=SECONDARY))
        labels.add(Text(str(k), font_size=25, color=INK).move_to(circle.get_center() + 2.16 * (RIGHT * np.cos(angle) + UP * np.sin(angle))))
    arrow = CurvedArrow(labels[2].get_center(), labels[0].get_center(), radius=-2.45, color=SECONDARY, stroke_width=5)
    calc = MathTex(r"12+18\\equiv 2+3\\equiv 0\\pmod 5", font_size=36, color=INK)
    calc.next_to(circle, DOWN, buff=0.42)
    return VGroup(glow(circle, ACCENT, 4), circle, ticks, labels, arrow, calc)


def symmetry_square_visual(beat: dict) -> VGroup:
    square = Square(side_length=2.55, color=ACCENT, stroke_width=5)
    vertices = VGroup(*[Dot(square.get_vertices()[i], color=SECONDARY) for i in range(4)])
    labels = VGroup(*[Text(str(i + 1), font_size=20, color=INK).next_to(vertices[i], square.get_vertices()[i] - square.get_center(), buff=0.12) for i in range(4)])
    rot = CurvedArrow(UP * 1.7 + RIGHT * 0.25, RIGHT * 1.7 + DOWN * 0.25, radius=-1.85, color=SECONDARY, stroke_width=5)
    mirror = DashedLine(UP * 1.85, DOWN * 1.85, color=PURPLE, stroke_width=3)
    formula = math_label(beat.get("math", r"D_4"), 31).next_to(square, DOWN, buff=0.45)
    return VGroup(glow(square, ACCENT, 4), square, vertices, labels, rot, mirror, formula)


def equivalence_partition_visual(beat: dict) -> VGroup:
    colors = [ACCENT, SECONDARY, GREEN]
    classes = VGroup()
    for i, color in enumerate(colors):
        blob = Ellipse(width=1.75, height=2.55, color=color, stroke_width=4).set_fill(color, 0.11)
        dots = VGroup(*[Dot(radius=0.055, color=INK).shift(RIGHT * ((j % 2) - 0.5) * 0.48 + UP * (j - 1) * 0.35) for j in range(3)])
        dots.move_to(blob)
        label = MathTex(rf"[{{i}}]", font_size=28, color=color).next_to(blob, DOWN, buff=0.14)
        classes.add(VGroup(glow(blob, color, 3), blob, dots, label))
    classes.arrange(RIGHT, buff=0.35)
    formula = math_label(beat.get("math", r"[a]"), 32).next_to(classes, DOWN, buff=0.42)
    return VGroup(classes, formula)


def closure_map_visual(beat: dict) -> VGroup:
    domain = Circle(radius=1.55, color=ACCENT, stroke_width=5).set_fill(ACCENT, 0.08)
    a = Dot(domain.get_center() + LEFT * 0.65 + UP * 0.35, color=INK)
    b = Dot(domain.get_center() + RIGHT * 0.15 + DOWN * 0.25, color=INK)
    c = Dot(domain.get_center() + RIGHT * 0.75 + UP * 0.05, color=SECONDARY)
    arrow1 = Arrow(a.get_center(), c.get_center(), buff=0.12, color=SECONDARY)
    arrow2 = Arrow(b.get_center(), c.get_center(), buff=0.12, color=SECONDARY)
    label = MathTex(r"a\\star b\\in S", font_size=34, color=INK).next_to(domain, DOWN, buff=0.42)
    orbit = Circle(radius=1.95, color=MUTED, stroke_width=1).set_opacity(0.45)
    return VGroup(glow(domain, ACCENT, 4), orbit, domain, a, b, c, arrow1, arrow2, label)


def operation_machine_visual(beat: dict) -> VGroup:
    left = VGroup(MathTex("a", color=ACCENT), MathTex("b", color=SECONDARY)).arrange(DOWN, buff=0.55)
    box = Circle(radius=0.78, color=PURPLE, stroke_width=5).set_fill(PURPLE, 0.13)
    op = MathTex(r"\\star", font_size=46, color=SECONDARY).move_to(box)
    out = MathTex(r"a\\star b", font_size=40, color=GREEN)
    group = VGroup(left, VGroup(glow(box, PURPLE, 4), box, op), out).arrange(RIGHT, buff=0.72)
    arrows = VGroup(
        Arrow(left[0].get_right(), box.get_left() + UP * 0.18, buff=0.1, color=ACCENT, stroke_width=4),
        Arrow(left[1].get_right(), box.get_left() + DOWN * 0.18, buff=0.1, color=SECONDARY, stroke_width=4),
        Arrow(box.get_right(), out.get_left(), buff=0.1, color=GREEN, stroke_width=4),
    )
    formula = math_label(beat.get("math", r"(a,b)\\mapsto a\\star b"), 32).next_to(group, DOWN, buff=0.45)
    return VGroup(group, arrows, formula)


def group_axioms_visual(beat: dict) -> VGroup:
    formulas = VGroup(
        MathTex(r"e\\star a=a", font_size=31, color=INK),
        MathTex(r"a\\star a^{-1}=e", font_size=31, color=INK),
        MathTex(r"(a\\star b)\\star c=a\\star(b\\star c)", font_size=31, color=INK),
    ).arrange(DOWN, aligned_edge=LEFT, buff=0.28)
    rings = VGroup(*[
        Circle(radius=1.0 + 0.35 * i, color=[ACCENT, SECONDARY, PURPLE][i], stroke_width=2).set_opacity(0.55)
        for i in range(3)
    ]).move_to(formulas)
    return VGroup(rings, formulas)


def coordinate_graph_visual(beat: dict) -> VGroup:
    axes = Axes(x_range=[-3, 3, 1], y_range=[-1, 5, 1], x_length=4.6, y_length=3.1, tips=False, axis_config={{"color": MUTED}})
    curve = axes.plot(lambda x: 0.45 * (x - 0.4) ** 2 + 0.25, x_range=[-2.6, 2.7], color=SECONDARY)
    tangent = Line(axes.c2p(-0.7, 0.8), axes.c2p(1.7, 1.9), color=ACCENT, stroke_width=4)
    label = MathTex(r"f(x)", font_size=30, color=INK).next_to(axes, UP, buff=0.1)
    return VGroup(axes, curve, tangent, label)


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
    return fit_to_safe_zone(item, max_width=8.8, max_height=5.3)


def beat_panel(beat: dict) -> VGroup:
    prose = safe_text(beat.get("onscreen") or beat.get("title", ""), font_size=28, width=5.4, max_lines=3)
    formula = math_label(beat.get("math", r"\\text{{idea}}"), 32)
    panel = VGroup(formula, prose).arrange(DOWN, aligned_edge=LEFT, buff=0.35)
    return fit_to_safe_zone(panel, max_width=5.8, max_height=4.4)


def floating_label(beat: dict) -> VGroup:
    signpost = Text(beat.get("onscreen") or beat.get("title", ""), font_size=25, color=INK)
    formula = MathTex(beat.get("math", r"\\text{{idea}}"), font_size=36, color=TITLE_COLOR)
    group = VGroup(formula, signpost).arrange(DOWN, buff=0.18)
    return fit_to_safe_zone(group, max_width=7.5, max_height=1.5)


def create_animation_for(mobject: Mobject):
    pieces = [part for part in mobject if isinstance(part, VMobject)]
    if len(pieces) > 1:
        return LaggedStart(*[Create(part) for part in pieces[:10]], lag_ratio=0.04)
    return GrowFromCenter(mobject)


class Lecture01(MovingCameraScene):
    def construct(self):
        self.camera.background_color = BLACK
        field = ambient_field()
        self.add(field)
        for beat in BEATS:
            audio = beat.get("audio")
            if audio:
                self.add_sound(audio)

            title = Text(beat.get("title", "Lecture"), font_size=34, weight=BOLD, color=TITLE_COLOR)
            title.to_corner(UL, buff=0.42)
            fit_to_safe_zone(title, max_width=6.5, max_height=0.75)
            visual = visual_for(beat)
            visual.move_to(ORIGIN)
            label = floating_label(beat).to_edge(DOWN, buff=0.42)

            duration = max(2.2, float(beat.get("duration", 4.0)))
            intro = min(1.1, duration * 0.2)
            motion = min(2.3, duration * 0.45)
            outro = min(0.65, duration * 0.16)
            hold = max(0.2, duration - intro - motion - outro)

            self.play(
                field.animate.set_opacity(0.55).shift(LEFT * 0.08),
                FadeIn(title, shift=RIGHT * 0.25),
                create_animation_for(visual),
                run_time=intro,
                rate_func=smooth,
            )
            self.play(
                Write(label[0]),
                FadeIn(label[1], shift=UP * 0.12),
                visual.animate.scale(1.05).shift(UP * 0.12),
                run_time=min(1.0, motion * 0.45),
                rate_func=smooth,
            )

            visual_kind = beat.get("visual", "")
            if visual_kind in ("modular_clock", "symmetry_square", "group_axioms"):
                self.play(Rotate(visual, angle=PI / 10, about_point=ORIGIN), run_time=motion, rate_func=there_and_back)
            elif visual_kind in ("equivalence_partition", "closure_map"):
                self.play(visual.animate.shift(RIGHT * 0.35).scale(1.06), field.animate.shift(RIGHT * 0.18), run_time=motion, rate_func=there_and_back)
            else:
                self.play(visual.animate.shift(LEFT * 0.28).scale(1.07), field.animate.shift(LEFT * 0.16), run_time=motion, rate_func=there_and_back)

            self.play(Circumscribe(label[0], color=SECONDARY, fade_out=True), run_time=min(0.9, hold))
            self.wait(max(0.01, hold - min(0.9, hold)))
            self.play(
                FadeOut(title, shift=LEFT * 0.18),
                FadeOut(label, shift=DOWN * 0.15),
                FadeOut(visual, scale=0.92),
                run_time=outro,
            )
            self.remove(title, visual, label)
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
