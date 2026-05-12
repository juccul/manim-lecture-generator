---
name: pdf-to-manim-lecture
description: Generate in-depth Manim lecture projects from one or more PDFs, including PDF extraction, curriculum planning, Manim scene code, synchronized TTS narration, and LaTeX math rendering. Use when the user asks to turn PDFs in the workspace or PDFs attached in chat into educational videos, Manim lectures, narrated math explainers, or a course.
---

# PDF To Manim Lecture

Use this skill when Codex should transform one PDF or a set of PDFs into a rigorous narrated Manim lecture or lecture series.

## Inputs

Accept any of these sources:

- PDF files in the current workspace, selected with `rg --files -g '*.pdf'`.
- A directory named by the user.
- PDF files attached in the chat, if available in the thread context or local attachments.
- A mixed set of workspace PDFs and attached PDFs.

If the user does not specify PDFs, discover workspace PDFs first. Ask only when there are no discoverable PDFs or when multiple unrelated PDF sets make the target ambiguous.

## Output Contract

Create a self-contained lecture project, normally under `manim_lectures/<slug>/`, with:

- `source_index.json`: discovered PDFs, page counts, extraction status, and warnings.
- `notes/extracted.md`: source-derived notes with page references.
- `notes/lecture_plan.md`: lecture objectives, prerequisites, scene sequence, key definitions, proofs, examples, and exercise prompts.
- `narration/script.md`: narration divided by scene and beat.
- `narration/timing.json`: estimated or generated speech timings for each beat.
- `scenes/*.py`: Manim scene files with LaTeX math, voiceover hooks, and clear scene boundaries.
- `assets/`: generated audio, derived figures, and any extracted images.
- `README.md`: setup, render commands, and dependency notes.
- `renders/lecture_01_narrated.mp4`: final narrated video when Manim and `ffmpeg` are available.

Final videos are the default target. If dependencies are missing, still create the complete project and clearly report which step could not run.

## End-To-End Baseline

Default to one-command generation. The script auto-creates or reuses a workspace `.venv` when dependencies are missing, then re-runs itself in that venv:

```bash
python3 plugins/manim-lecture-generator/scripts/generate_lecture.py <pdf-or-directory> --out manim_lectures/<slug> --quality=-ql
```

Do not install Python packages globally. If the user wants manual setup, use:

```bash
bash plugins/manim-lecture-generator/scripts/setup_venv.sh
fmtutil-user --byfmt latex
```

This creates a finished baseline video without an external API. Codex should improve the generated plan, narration, and Manim scenes when the user asks for an in-depth lecture, then rerun TTS/render/mux/validation.

## Workflow

1. Inventory the PDFs.
   - Use `scripts/pdf_inventory.py` from this plugin if available.
   - Use `scripts/extract_pdf_text.py` from this plugin for first-pass Markdown extraction.
   - Prefer structured extraction with `pypdf`, `pymupdf`, `pdftotext`, or existing local utilities.
   - Preserve page references for every definition, theorem, proof, example, and figure claim.

2. Build a teaching plan before writing animation code.
   - Decide the lecture granularity: one long lecture, several short scenes, or a multi-video series.
   - Identify prerequisites and the mathematical through-line.
   - Convert dense source text into teachable steps: motivation, definition, intuition, formal statement, worked example, proof sketch or full proof, recap.
   - Call out gaps where OCR or PDF extraction was weak.
   - Treat extracted PDF text as source evidence only. Do not narrate raw slide text, page footers, bullet fragments, OCR artifacts, or copied paragraphs.
   - Author the lesson in natural spoken language. The narration should sound like a teacher explaining the idea, not like someone reading the slides aloud.

3. Write narration for timing.
   - Keep each narration beat short enough to synchronize with one visual action.
   - Mark math expressions in LaTeX.
   - Use stable beat IDs such as `L01_S03_B02`.
   - Target a spoken pace of about 135-155 words per minute unless the user requests another pace.

4. Generate TTS.
   - Default to local Kokoro TTS through `scripts/tts_kokoro.py`.
   - Use `af_heart` as the default voice unless the user requests a different voice.
   - Kokoro is the preferred default because it offers the best current practical balance of speed, voice quality, CPU-friendly operation, and setup simplicity for local lecture narration.
   - Use Chatterbox only when the user explicitly prioritizes voice cloning or emotional control and accepts heavier setup/runtime costs.
   - Save one audio file per beat or per scene, plus a JSON timing manifest.
   - Do not hard-code API keys. The default Kokoro path is local and should not require credentials.

5. Implement Manim scenes.
   - Use `MathTex`/`Tex` for math and definitions.
   - Keep mathematical notation consistent with the source PDFs.
   - Use visual pacing: reveal, transform, highlight, arrange, then recap.
   - Prefer clear diagrams and algebraic transformations over text-heavy slides.
   - Do not follow a fixed visual template when Codex is authoring the final lecture. Design visuals from the actual mathematical content: number lines, clocks, Cayley tables, commutative diagrams, graphs, geometric transformations, set partitions, proof flow diagrams, and concrete examples as appropriate.
   - Aim for a 3Blue1Brown-like teaching style: black background, dynamic object transformations, color-coded mathematical roles, glowing highlights, smooth camera movement, progressive reveals, and minimal text.
   - Avoid rigid title/body/footer layouts and repeated slide frames. Prefer a fluid stage where objects enter, transform, orbit, split, merge, and fade as the narration advances.
   - On-screen text should be labels, definitions, formulas, or short signposts only. Never put raw PDF paragraphs or slide bullets on screen.
   - Use Manim Voiceover if installed; otherwise structure code so audio can be attached later with `self.add_sound(...)` and timing waits.
   - Use safe-zone helpers for every text, math, diagram, and group. No visible object may overlap incoherently, exceed the render frame, or be sized too small/large for the viewport.

6. Render and mux.
   - After TTS generation, regenerate the Manim scene from the updated `narration/timing.json` so `self.wait(...)` uses real audio durations, not estimates.
   - Render with Manim, normally `manim -ql scenes/lecture_01.py Lecture01` for preview and higher quality only after validation.
   - Concatenate generated beat audio according to `start`, `duration`, and `end` in `narration/timing.json`, inserting silence for any intended gaps.
   - Save the final narrated file under `renders/`.

## Audio Sync Requirements

- Treat `narration/timing.json` as the single source of truth after TTS runs.
- Measure actual WAV duration from the generated files and write it back to `duration`.
- Recompute cumulative `start` and `end` after measuring audio.
- Regenerate Manim scenes after TTS and before rendering.
- Scene waits must match actual narration duration for each beat.
- Muxed audio must be assembled from the same timing file used by the rendered scene.

7. Validate.
   - Run syntax checks on generated Python.
   - Render at least one short low-quality preview when feasible, for example `manim -ql scenes/lecture_01.py Lecture01`.
   - Check that LaTeX compiles. If LaTeX fails, simplify the expression or use supported packages/macros.
   - Verify audio paths and timing IDs match scene code.
   - Run `scripts/validate_manim_layout.py <project>` after rendering.
   - Manually inspect rendered frames or screenshots when making custom scenes.
   - Fix any overlap, clipping, off-frame object, unreadable font size, or blank/cropped render before delivering.

## Project Conventions

Prefer this layout:

```text
manim_lectures/<slug>/
  README.md
  pyproject.toml
  source_index.json
  notes/
  narration/
  scenes/
  assets/
```

Generated Manim code should be idiomatic Python and runnable from the project root. Keep scenes composable: one class per major lecture segment, with helper functions only when they reduce real duplication.

## Layout Safety Requirements

These are hard requirements for generated Manim scenes:

- Define frame constants and safe-zone dimensions.
- Scale every `Text`, `Paragraph`, `Tex`, `MathTex`, image, diagram, and `VGroup` to fit inside the safe zone before animation.
- Use `arrange(...)` with explicit `buff` for stacked objects.
- Never place independent objects manually where their bounding boxes can collide; use `next_to`, `arrange`, grids, or a local layout helper.
- Keep title, body, and footer areas separate.
- Avoid more than five lines of paragraph text on screen at once.
- Break long formulas into aligned steps or multiple beats.
- Before final delivery, render and validate the actual video, not just the Python syntax.

## Math And LaTeX Guidance

- Use `MathTex` for formulas and `Tex` for prose.
- Define common macros in a shared helper when many scenes need them.
- Avoid unsupported LaTeX packages unless the local Manim template explicitly supports them.
- For aligned derivations, use `MathTex(r"\begin{aligned} ... \end{aligned}")`.
- For long proofs, animate the logical structure and only show essential lines on screen.

## TTS Synchronization Guidance

Timing manifests should include:

```json
{
  "beat_id": "L01_S01_B01",
  "scene": "Lecture01Intro",
  "text": "Narration text.",
  "audio": "assets/audio/L01_S01_B01.wav",
  "duration": 4.2,
  "start": 0.0,
  "end": 4.2
}
```

If real TTS generation is unavailable, create estimated timing from word count and leave the audio field null. Make the fallback explicit in the README and final response.

## Quality Bar

- The lecture should teach, not merely summarize.
- PDF material should be transformed into a coherent lesson. Copying slide text into narration or visuals is a failure.
- Every theorem or definition used in the animation should be introduced before use.
- Important equations should be derived step by step.
- The final deliverable should include concrete render commands and known limitations.
- Mention any pages that could not be extracted or any figures that need manual recreation.
