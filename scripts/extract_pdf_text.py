#!/usr/bin/env python3
"""Extract text from PDFs into page-referenced Markdown."""

from __future__ import annotations

import argparse
import subprocess
import tempfile
from pathlib import Path


def extract_with_pypdf(path: Path) -> list[str]:
    from pypdf import PdfReader

    reader = PdfReader(str(path))
    return [(page.extract_text() or "").strip() for page in reader.pages]


def extract_with_pdftotext(path: Path) -> str:
    with tempfile.TemporaryDirectory() as tmp:
        output = Path(tmp) / "out.txt"
        subprocess.run(["pdftotext", "-layout", str(path), str(output)], check=True)
        return output.read_text(encoding="utf-8", errors="replace").strip()


def main() -> None:
    parser = argparse.ArgumentParser(description="Extract PDF text for Manim lecture planning.")
    parser.add_argument("pdfs", nargs="+", help="PDF files to extract.")
    parser.add_argument("--output", default="notes/extracted.md", help="Output Markdown path.")
    args = parser.parse_args()

    output = Path(args.output).expanduser()
    output.parent.mkdir(parents=True, exist_ok=True)

    sections: list[str] = []
    for raw_pdf in args.pdfs:
        pdf = Path(raw_pdf).expanduser().resolve()
        sections.append(f"# {pdf.name}\n")
        try:
            pages = extract_with_pypdf(pdf)
            for index, text in enumerate(pages, start=1):
                sections.append(f"\n## {pdf.name} - page {index}\n\n{text}\n")
        except Exception as pypdf_exc:
            try:
                text = extract_with_pdftotext(pdf)
                sections.append(
                    "\n"
                    f"> Page boundaries unavailable from fallback extraction. "
                    f"pypdf error: {type(pypdf_exc).__name__}: {pypdf_exc}\n\n"
                    f"{text}\n"
                )
            except Exception as fallback_exc:
                sections.append(
                    "\n"
                    f"> Extraction failed. pypdf error: {type(pypdf_exc).__name__}: {pypdf_exc}; "
                    f"pdftotext error: {type(fallback_exc).__name__}: {fallback_exc}\n"
                )

    output.write_text("\n".join(sections).strip() + "\n", encoding="utf-8")
    print(f"Wrote extracted text to {output}.")


if __name__ == "__main__":
    main()
