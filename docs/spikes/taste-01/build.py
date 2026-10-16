#!/usr/bin/env python3
"""Build hero.html from hero.template.html.

Embeds latin-subset woff2 fonts from console/node_modules/@fontsource/* as
base64 data-URLs so the output is a single self-contained file with zero
network requests at runtime.

Usage:  python docs/spikes/taste-01/build.py
"""

from __future__ import annotations

import base64
import pathlib
import sys

ROOT = pathlib.Path("/home/openclaw/projects/archivist")
HERE = pathlib.Path(__file__).parent

FONTS = {
    "__FONT_INSTRUMENT_SERIF_400__": ROOT
    / "console/node_modules/@fontsource/instrument-serif/files/instrument-serif-latin-400-normal.woff2",
    "__FONT_INSTRUMENT_SERIF_400I__": ROOT
    / "console/node_modules/@fontsource/instrument-serif/files/instrument-serif-latin-400-italic.woff2",
    "__FONT_INTER_400__": ROOT
    / "console/node_modules/@fontsource/inter/files/inter-latin-400-normal.woff2",
    "__FONT_INTER_500__": ROOT
    / "console/node_modules/@fontsource/inter/files/inter-latin-500-normal.woff2",
    "__FONT_JETBRAINS_MONO_400__": ROOT
    / "console/node_modules/@fontsource/jetbrains-mono/files/jetbrains-mono-latin-400-normal.woff2",
    "__FONT_JETBRAINS_MONO_500__": ROOT
    / "console/node_modules/@fontsource/jetbrains-mono/files/jetbrains-mono-latin-500-normal.woff2",
}


def main() -> int:
    template_path = HERE / "hero.template.html"
    out_path = HERE / "hero.html"

    if not template_path.is_file():
        print(f"missing template: {template_path}", file=sys.stderr)
        return 1

    tpl = template_path.read_text(encoding="utf-8")
    for placeholder, font_path in FONTS.items():
        if not font_path.is_file():
            print(f"missing font: {font_path}", file=sys.stderr)
            return 1
        b64 = base64.b64encode(font_path.read_bytes()).decode("ascii")
        if placeholder not in tpl:
            print(f"placeholder not found: {placeholder}", file=sys.stderr)
            return 1
        tpl = tpl.replace(placeholder, b64)

    out_path.write_text(tpl, encoding="utf-8")
    print(f"wrote {out_path} ({out_path.stat().st_size // 1024} KiB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
