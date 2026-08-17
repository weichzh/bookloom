#!/usr/bin/env python3
"""Compatibility CLI for the manifest-driven LaTeX EPUB adapter."""

from __future__ import annotations

import argparse
import re
from pathlib import Path

from translator import CliError, build_latex_epub_source


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--title", required=True)
    parser.add_argument("--author")
    parser.add_argument("--language", default="zh-CN")
    parser.add_argument("--identifier")
    parser.add_argument("--cover", type=Path)
    args = parser.parse_args()

    root = Path(__file__).resolve().parents[1]
    source = args.source.resolve()
    output = args.output.resolve(strict=False)
    work = source.parent.parent
    cover = (
        args.cover.resolve()
        if args.cover
        else work / "assets" / "cover-p1-original.png"
    )
    if not source.is_file():
        parser.error(f"source does not exist: {source}")
    if source.suffix.lower() != ".tex":
        parser.error(f"source must be a .tex file: {source}")
    if output.suffix.lower() != ".epub":
        parser.error(f"output must be an .epub file: {output}")
    if not cover.is_file():
        parser.error(f"cover does not exist: {cover}")
    identifier = args.identifier or (
        "urn:translator:"
        + re.sub(r"[^a-z0-9._-]+", "-", work.name.lower()).strip("-")
        + ":"
        + args.language
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    try:
        build_latex_epub_source(
            root=root,
            work_path=work,
            entry=source,
            output=output,
            title=args.title,
            author=args.author,
            language=args.language,
            identifier=identifier,
            cover=cover,
        )
    except CliError as error:
        parser.error(str(error))
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
