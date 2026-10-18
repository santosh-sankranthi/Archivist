#!/usr/bin/env python3
"""Ingest Markdown/MDX docs into FalkorDB as DocPage nodes.

Usage:
    python scripts/ingest_docs.py [--docs PATH] [--graph NAME] [--base-url URL]
                                  [--rebuild] [--json]

Defaults target the FalkorDB docs checkout kept next to the repo:
    docs   /home/openclaw/projects/falkordb-docs
    graph  repo:falkordb-py
    url    https://docs.falkordb.com

Each (file, section) pair becomes one `(:DocPage {id, path, title, section,
url, text, chars, ts})` node, idempotent on `id`. Sections split on `## `
headings; oversized sections split again at paragraph boundaries (~2000
chars). This is the raw material for the agent's `search_docs` tool.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

try:
    from dotenv import load_dotenv

    load_dotenv(ROOT / ".env")
except ImportError:
    pass

from falkordb import FalkorDB

CHUNK_CAP = 2000
SKIP_DIRS = {"images", "scripts", "node_modules", ".git", "snippets"}
INLINE_TAG = re.compile(r"</?[A-Z][A-Za-z0-9]*(?:\s[^<>]*)?/?>")
IMPORT_LINE = re.compile(r"^\s*import\s.+$")


def parse_frontmatter(text: str) -> tuple[dict, str]:
    meta: dict[str, str] = {}
    if not text.startswith("---"):
        return meta, text
    end = text.find("\n---", 3)
    if end == -1:
        return meta, text
    for line in text[3:end].splitlines():
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        value = value.strip().strip('"').strip("'")
        if key.strip() in {"title", "sidebarTitle", "description"}:
            meta[key.strip()] = value
    return meta, text[end + 4 :].lstrip("\n")


def clean_body(body: str) -> str:
    lines = []
    for line in body.splitlines():
        if IMPORT_LINE.match(line):
            continue
        lines.append(INLINE_TAG.sub("", line))
    text = "\n".join(lines)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def split_sections(body: str) -> list[tuple[str, str]]:
    sections: list[tuple[str, str]] = []
    current_title = "Overview"
    current: list[str] = []
    for line in body.splitlines():
        if line.startswith("## "):
            sections.append((current_title, "\n".join(current).strip()))
            current_title = line[3:].strip()
            current = []
        else:
            current.append(line)
    sections.append((current_title, "\n".join(current).strip()))
    return [(title, text) for title, text in sections if text]


def split_long(section: str, text: str) -> list[tuple[str, str]]:
    if len(text) <= CHUNK_CAP:
        return [(section, text)]
    parts: list[tuple[str, str]] = []
    buf: list[str] = []
    size = 0
    for para in text.split("\n\n"):
        if size + len(para) + 2 > CHUNK_CAP and buf:
            parts.append((section if not parts else f"{section} (cont.)", "\n\n".join(buf)))
            buf, size = [], 0
        buf.append(para)
        size += len(para) + 2
    if buf:
        parts.append((section if not parts else f"{section} (cont.)", "\n\n".join(buf)))
    return parts


def url_for(base_url: str, rel: str) -> str:
    rel = rel[:-4] if rel.endswith(".mdx") else rel[:-3] if rel.endswith(".md") else rel
    if rel.endswith("/index"):
        rel = rel[: -len("index")]
    return f"{base_url.rstrip('/')}/{rel.strip('/')}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Ingest docs into FalkorDB")
    parser.add_argument("--docs", type=Path, default=Path("/home/openclaw/projects/falkordb-docs"))
    parser.add_argument("--graph", default="repo:falkordb-py")
    parser.add_argument("--base-url", default="https://docs.falkordb.com")
    parser.add_argument("--host", default=os.environ.get("FALKORDB_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("FALKORDB_PORT", "6379")))
    parser.add_argument("--rebuild", action="store_true", help="delete existing :DocPage nodes first")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    if not args.docs.is_dir():
        print(f"docs dir not found: {args.docs}", file=sys.stderr)
        return 2

    db = FalkorDB(host=args.host, port=args.port)
    graph = db.select_graph(args.graph)

    if args.rebuild:
        graph.query("MATCH (d:DocPage) DETACH DELETE d")

    now = int(time.time())
    files = chunks = skipped = 0
    errors: list[str] = []

    for path in sorted(args.docs.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(args.docs).as_posix()
        if path.suffix not in {".mdx", ".md"}:
            continue
        if any(part in SKIP_DIRS for part in rel.split("/")):
            continue
        if "/" not in rel and path.name != "index.mdx":
            skipped += 1
            continue
        try:
            raw = path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            errors.append(f"{rel}: {exc}")
            continue
        meta, body = parse_frontmatter(raw)
        body = clean_body(body)
        title = meta.get("title") or meta.get("sidebarTitle") or rel
        url = url_for(args.base_url, rel)
        pieces: list[tuple[str, str]] = []
        for section, text in split_sections(body):
            pieces.extend(split_long(section, text))
        for idx, (section, text) in enumerate(pieces):
            doc_id = f"{rel}#{idx}"
            try:
                graph.query(
                    "MERGE (d:DocPage {id: $id}) "
                    "SET d.path = $path, d.title = $title, d.section = $section, "
                    "d.url = $url, d.text = $text, d.chars = $chars, d.ts = $ts",
                    {
                        "id": doc_id,
                        "path": rel,
                        "title": title,
                        "section": section,
                        "url": url,
                        "text": text,
                        "chars": len(text),
                        "ts": now,
                    },
                )
                chunks += 1
            except Exception as exc:  # noqa: BLE001 - report and continue
                errors.append(f"{doc_id}: {exc}")
        files += 1

    summary = {
        "graph": args.graph,
        "files": files,
        "chunks": chunks,
        "skipped_root": skipped,
        "errors": errors[:10],
        "error_count": len(errors),
    }
    if args.json:
        print(json.dumps(summary, indent=2))
    else:
        print(
            f"docs ingest -> {args.graph}: {files} files, {chunks} DocPage chunks, "
            f"{len(errors)} errors"
        )
        for err in errors[:10]:
            print(f"  ! {err}")
    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
