# Manim Lecture Generator

Codex plugin for turning PDFs into finished narrated Manim lecture videos with synchronized local TTS and LaTeX-heavy math scenes.

The main workflow lives in `skills/pdf-to-manim-lecture/SKILL.md`.

## End-To-End Command

Default one-command usage:

```bash
python3 plugins/manim-lecture-generator/scripts/generate_lecture.py *.pdf --out manim_lectures/algebra --quality=-ql
```

If Kokoro, Manim, or SoundFile are missing, the generator automatically creates
or reuses `.venv` in the workspace, installs `requirements.txt`, and re-runs
itself inside that venv. It does not install Python packages globally.

Manual setup remains available when you want to prepare the environment first:

```bash
bash plugins/manim-lecture-generator/scripts/setup_venv.sh
fmtutil-user --byfmt latex
```

The generator creates a project, extracts source text, writes a baseline Manim scene, generates local narration with Kokoro when available, renders with Manim, muxes audio with `ffmpeg`, and validates rendered frames. The baseline scene detects mathematical topics from the source, then writes natural-language narration and short formula-focused visuals instead of reading or displaying raw PDF text.

The pipeline measures generated WAV durations, rewrites `narration/timing.json`,
regenerates the Manim scene from those real durations, and then muxes the final
MP4 from the same timing manifest to prevent cumulative narration drift.
It also writes `narration/tts_status.json`, which records whether each beat used
Kokoro or fell back to generated silence.

Use `--workspace <path>` to choose where `.venv` is created. Use
`--no-bootstrap` to require the current Python environment.

## Helper Scripts

- `scripts/generate_lecture.py`: end-to-end PDF to narrated Manim video pipeline.
- `scripts/pdf_inventory.py`: creates a JSON index of PDFs and page counts.
- `scripts/extract_pdf_text.py`: extracts PDF text into page-referenced Markdown when possible.
- `scripts/estimate_timing.py`: estimates narration beat timing from a Markdown script.
- `scripts/tts_kokoro.py`: generates local TTS audio with Kokoro, falling back to silence placeholders when dependencies are missing.
- `scripts/mux_narration.py`: concatenates beat WAVs and muxes narration into the rendered MP4.
- `scripts/validate_manim_layout.py`: performs basic rendered-video validation.

## Local TTS Default

The plugin defaults to Kokoro because it is currently a strong local balance of speed, speech quality, CPU-friendliness, and setup effort. Install it with:

```bash
.venv/bin/python -m pip install -r plugins/manim-lecture-generator/requirements.txt
sudo apt-get install espeak-ng ffmpeg
```

Chatterbox can be added later as an optional voice-cloning backend, but Kokoro is the default for reliable lecture generation.

Default voice: `af_heart`. Override it with `--voice <kokoro_voice_name>`.
