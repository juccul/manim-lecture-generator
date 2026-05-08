#!/usr/bin/env python3
"""Create a JSON inventory for PDF sources."""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path


def count_pages(path: Path) -> tuple[int | None, str | None]:
    try:
        from pypdf import PdfReader

        return len(PdfReader(str(path)).pages), None
    except Exception as exc:
        pypdf_error = f"{type(exc).__name__}: {exc}"

    try:
        result = subprocess.run(
            ["pdfinfo", str(path)],
            check=True,
            capture_output=True,
            text=True,
        )
        for line in result.stdout.splitlines():
            if line.startswith("Pages:"):
                return int(line.split(":", 1)[1].strip()), None
    except Exception as exc:
        return None, f"pypdf failed ({pypdf_error}); pdfinfo failed ({type(exc).__name__}: {exc})"

    return None, f"pypdf failed ({pypdf_error}); pdfinfo did not report pages"


def main() -> None:
    parser = argparse.ArgumentParser(description="Inventory PDF files for lecture generation.")
    parser.add_argument("paths", nargs="*", help="PDF files or directories. Defaults to cwd.")
    parser.add_argument("--output", default="source_index.json", help="Output JSON path.")
    args = parser.parse_args()

    roots = [Path(p).expanduser() for p in args.paths] or [Path.cwd()]
    pdfs: list[Path] = []
    for root in roots:
        if root.is_dir():
            pdfs.extend(sorted(root.rglob("*.pdf")))
        elif root.suffix.lower() == ".pdf":
            pdfs.append(root)

    seen: set[Path] = set()
    entries = []
    for pdf in pdfs:
        resolved = pdf.resolve()
        if resolved in seen:
            continue
        seen.add(resolved)
        pages, error = count_pages(resolved)
        entries.append(
            {
                "path": str(resolved),
                "name": resolved.name,
                "pages": pages,
                "page_count_error": error,
            }
        )

    output = Path(args.output).expanduser()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps({"pdfs": entries}, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {output} with {len(entries)} PDF(s).")


if __name__ == "__main__":
    main()
