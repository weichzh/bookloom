"""Render a source page in the frozen MuPDF display-space convention."""
from __future__ import annotations


def render(source, page, dpi, runtime, directory, run):
    paths = {}
    for fmt, suffix in (("png", "png"), ("svg", "svg"), ("stext", "stext.xml"),
                        ("trace", "trace.xml")):
        path = directory / f"source.{suffix}"
        run([runtime["mutool"], "draw", "-q", "-b", "CropBox", "-F", fmt,
             "-r", str(dpi if fmt == "png" else 72), "-o", path, source, str(page)], directory)
        if not path.is_file():
            raise ValueError(f"source renderer did not create {fmt}")
        paths[fmt] = path
    return paths
