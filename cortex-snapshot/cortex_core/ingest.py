"""Bulk-ingest: walk a scattered directory tree into the Cortex corpus (gap I1).

Every prior ingestion path is single-URL/manual (``cortex-fetch``). This is the
directory front door: point it at "a huge scattered pile" and it extracts text
from common file types, dedupes identical content, materializes each doc as a
``.md`` file under a corpus shard the EXISTING index already discovers, and (by
default) rebuilds that index so the content is immediately searchable.

It reuses the existing chunker/indexer (``CortexSearchIndex``) and the existing
HTML->text extractor (``fetch._html_to_text``) -- it reimplements no retrieval.
Deterministic, resumable, idempotent (re-ingest = no dupes). See
``docs/INGEST-SPEC.md``.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path

from .config import make_stdio_encoding_safe, resolve_workspace_override
from .fetch import _html_to_text
from .search import _EXCLUDED_DIR_NAMES, CortexSearchIndex

# Allowlist-driven extraction: an unknown extension is skipped as
# ``unsupported_type`` so a random binary never reaches the chunker.
TEXT_EXTENSIONS = frozenset(
    {
        ".md", ".markdown", ".txt", ".rst", ".py", ".json", ".yaml", ".yml",
        ".toml", ".ini", ".cfg", ".csv", ".tsv", ".log", ".sql", ".sh",
        ".js", ".ts", ".tsx", ".jsx", ".css", ".c", ".h", ".cpp", ".go",
        ".rs", ".java", ".rb", ".php", ".xml",
    }
)
HTML_EXTENSIONS = frozenset({".html", ".htm"})
PDF_EXTENSIONS = frozenset({".pdf"})

# A single very large file shouldn't blow the corpus; mirrors fetch's per-doc
# spirit (MAX_FETCH_BYTES = 10 MiB). Configurable via ``--max-bytes``.
DEFAULT_MAX_BYTES = 10 * 1024 * 1024

_OUTPUT_SHARD = "cortex-ingest"  # under docs/ -> matches the index's docs/cortex-* glob
_MANIFEST_REL = ("library", "cortex-library", "ingest-manifest.json")
_SLUG_MAX_LEN = 80


def _extract_pdf(path: Path) -> str | None:
    """Optional PDF text extraction. Returns None (skip) when no extractor is
    importable -- never a hard dependency, never a crash. Stdlib has no PDF text
    path; scanned/image PDFs need OCR (gap I2), out of scope here."""
    try:
        import pypdf  # type: ignore
    except Exception:
        return None
    try:
        reader = pypdf.PdfReader(str(path))
        parts = [page.extract_text() or "" for page in reader.pages]
    except Exception:
        return None
    text = "\n\n".join(p.strip() for p in parts if p.strip())
    return text or None


def _extract_text(path: Path, max_bytes: int) -> tuple[str | None, str | None]:
    """Extract plain text from a single file.

    Returns ``(text, None)`` on success or ``(None, reason)`` when the file is
    skipped. ``reason`` is one of: ``unsupported_type``, ``oversize``,
    ``binary_content``, ``pdf_no_extractor``, ``empty``, ``unreadable``.
    """
    ext = path.suffix.lower()
    try:
        size = path.stat().st_size
    except OSError:
        return None, "unreadable"
    if size > max_bytes:
        return None, "oversize"

    if ext in PDF_EXTENSIONS:
        text = _extract_pdf(path)
        if text is None:
            return None, "pdf_no_extractor"
        return text, None

    if ext not in TEXT_EXTENSIONS and ext not in HTML_EXTENSIONS:
        return None, "unsupported_type"

    try:
        raw = path.read_bytes()
    except OSError:
        return None, "unreadable"
    # Second guard: a mislabeled binary (allowlisted extension but NUL bytes)
    # must not poison the corpus.
    if b"\x00" in raw:
        return None, "binary_content"
    text = raw.decode("utf-8", errors="replace")

    if ext in HTML_EXTENSIONS:
        text = _html_to_text(text)
    text = text.strip()
    if not text:
        return None, "empty"
    return text, None


def _slug(rel: Path) -> str:
    import re

    flat = "-".join(rel.with_suffix("").parts)
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", flat.lower()).strip("-")
    return (slug[:_SLUG_MAX_LEN].strip("-")) or "doc"


def _iter_files(root: Path):
    """Deterministically walk ``root``, pruning excluded/hidden directories."""
    for dirpath, dirnames, filenames in os.walk(root):
        # Prune in place so os.walk never descends junk/hidden dirs.
        dirnames[:] = sorted(
            d for d in dirnames if d not in _EXCLUDED_DIR_NAMES and not d.startswith(".")
        )
        for name in sorted(filenames):
            yield Path(dirpath) / name


def _load_manifest(path: Path) -> dict:
    if not path.exists():
        return {"version": 1, "entries": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict) and isinstance(data.get("entries"), dict):
            return data
    except (OSError, json.JSONDecodeError):
        pass
    return {"version": 1, "entries": {}}


def _save_manifest(path: Path, manifest: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
    os.replace(tmp, path)  # atomic -> resumable, never a half-written manifest


def ingest_dir(
    root: str | Path,
    workspace: str | Path | None = None,
    *,
    reindex: bool = True,
    max_bytes: int = DEFAULT_MAX_BYTES,
) -> dict:
    """Walk ``root``, extract+materialize supported files into the workspace
    corpus, dedupe identical content, and (by default) rebuild the search index.

    Returns ``{files_seen, ingested, skipped, deduped, skipped_reasons,
    output_dir}``. Idempotent: re-ingesting the same tree writes zero new docs.
    """
    root = Path(root).expanduser()
    if not root.is_dir():
        raise NotADirectoryError(f"ingest root is not a directory: {root}")

    ws = resolve_workspace_override(workspace)
    out_dir = ws / "docs" / _OUTPUT_SHARD
    out_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = ws
    for part in _MANIFEST_REL:
        manifest_path = manifest_path / part
    manifest = _load_manifest(manifest_path)
    entries: dict = manifest["entries"]

    # Reverse indexes over the existing manifest.
    hash_to_out = {v["hash"]: out for out, v in entries.items()}
    source_to_out = {v["source"]: out for out, v in entries.items()}

    files_seen = 0
    ingested = 0
    deduped = 0
    skipped = 0
    skipped_reasons: dict[str, int] = {}

    def _skip(reason: str) -> None:
        nonlocal skipped
        skipped += 1
        skipped_reasons[reason] = skipped_reasons.get(reason, 0) + 1

    now = datetime.now(timezone.utc).isoformat()

    for path in _iter_files(root):
        files_seen += 1
        text, reason = _extract_text(path, max_bytes)
        if text is None:
            _skip(reason or "unreadable")
            continue

        content_hash = hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()
        source_key = str(path.resolve())

        prev_out = source_to_out.get(source_key)
        if prev_out is not None:
            prev_hash = entries[prev_out]["hash"]
            if prev_hash == content_hash:
                deduped += 1  # unchanged re-ingest of a known source
                continue
            # Source changed: overwrite the SAME output file, retire old hash.
            hash_to_out.pop(prev_hash, None)
            out_rel = prev_out
        elif content_hash in hash_to_out:
            deduped += 1  # byte-identical content from a different source path
            continue
        else:
            # New source, new content: allocate a stable output filename.
            rel = path.relative_to(root)
            base = _slug(rel)
            out_rel = f"{base}-{content_hash[:8]}.md"
            # Defensive: distinct content colliding on the truncated name.
            suffix = 1
            while out_rel in entries and entries[out_rel]["hash"] != content_hash:
                out_rel = f"{base}-{content_hash[:8]}-{suffix}.md"
                suffix += 1

        doc = (
            "---\n"
            f"source_path: {json.dumps(source_key)}\n"
            f"content_hash: {json.dumps(content_hash)}\n"
            f"ingested_at: {json.dumps(now)}\n"
            "---\n\n"
            + text
            + "\n"
        )
        (out_dir / out_rel).write_text(doc, encoding="utf-8")
        entries[out_rel] = {"source": source_key, "hash": content_hash}
        hash_to_out[content_hash] = out_rel
        source_to_out[source_key] = out_rel
        ingested += 1

    _save_manifest(manifest_path, manifest)

    if reindex:
        CortexSearchIndex(ws).rebuild()

    return {
        "files_seen": files_seen,
        "ingested": ingested,
        "skipped": skipped,
        "deduped": deduped,
        "skipped_reasons": skipped_reasons,
        "output_dir": str(out_dir),
    }


def main(argv: list[str] | None = None) -> int:
    make_stdio_encoding_safe()
    parser = argparse.ArgumentParser(
        description="Bulk-ingest a directory tree into the Cortex corpus (gap I1)."
    )
    parser.add_argument("directory", help="root directory to ingest")
    parser.add_argument("--workspace", default=None)
    parser.add_argument(
        "--no-reindex",
        action="store_true",
        help="skip the index rebuild (the next cortex-search auto-rebuilds when stale)",
    )
    parser.add_argument(
        "--max-bytes",
        type=int,
        default=DEFAULT_MAX_BYTES,
        help=f"per-file byte cap (default {DEFAULT_MAX_BYTES})",
    )
    args = parser.parse_args(argv)

    root = Path(args.directory).expanduser()
    if not root.is_dir():
        print(f"error: not a directory: {root}")
        return 2

    result = ingest_dir(
        root,
        workspace=args.workspace,
        reindex=not args.no_reindex,
        max_bytes=args.max_bytes,
    )
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
