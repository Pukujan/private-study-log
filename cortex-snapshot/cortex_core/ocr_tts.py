"""OCR + TTS ingest lane -- the "Kurzweil 3000" milestone (gap I2).

Turn a scanned page / image into corpus text (OCR), and text into speech (TTS).
This is the accessibility front door to the same corpus the bulk directory
ingest (``cortex-ingest``, gap I1) and single-URL fetch (``cortex-fetch``) feed:
OCR'd text is materialized as a ``.md`` doc under the ``docs/cortex-ingest/``
shard the EXISTING index already discovers, chunks, and ranks -- this
reimplements no retrieval.

Cardinal rule (anti-evidence-theater): **OCR NEVER fabricates text.** When no
OCR backend is available, or recognition yields nothing, the page is
*quarantined* (a record is appended to ``ocr-quarantine.jsonl``) and **no doc
enters the corpus**. Inventing "extracted" text would poison the very corpus
this project exists to keep trustworthy. TTS is real when an engine is present
and a clearly-labeled no-op (quarantined, no fake wav) when not.

The OCR path is **PLUGGABLE** with a strongest-first preference order; every
backend is OPTIONAL, degrades gracefully, and is honestly labeled. The engine
that actually recognized a page is recorded per page (provenance -- the doc's
``ocr_engine`` / ``ocr_backend`` frontmatter and the ``OcrResult.backend``):

  1. ``vlm``       -- VLM / LLM OCR. Routes the image to a vision model (e.g.
                      Qwen-VL) through an OpenAI-compatible ``/chat/completions``
                      endpoint, reusing ``judge.py``'s API-tier ``.env`` config
                      pattern (``VLM_OCR_API_URL`` / ``VLM_OCR_API_KEY`` /
                      ``VLM_OCR_MODEL``). Strongest on messy / low-quality scans.
                      No extra Python dep -- reuses ``httpx`` (already core via
                      anthropic). Respects the recorded 12000 ``max_tokens``
                      floor (``judge.MIN_MAX_TOKENS_BY_TIER``).
  2. ``paddleocr`` -- Strong LOCAL neural OCR, if ``paddleocr`` is installed
                      (``pip install -e .[ocr-paddle]``). Offline, no API key.
  3. ``tesseract`` -- ``pytesseract`` + ``Pillow`` driving a system ``tesseract``
                      binary -- the last-resort floor. The wrapper being
                      importable is NOT enough; the binary must be on PATH
                      (``get_tesseract_version()`` is the real probe).

TTS is ``pyttsx3`` (Windows SAPI5 / macOS NSSpeech / espeak on Linux). Offline,
no network, no API key.

Install the local extras: ``pip install -e .[ocr]`` (Tesseract wrapper + Pillow
+ pyttsx3) and/or ``pip install -e .[ocr-paddle]`` (PaddleOCR). The VLM backend
needs only ``.env`` config, no extra install. CLI-only (``cortex-ocr``) per the
MCP anti-bloat decision -- no new MCP tool.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import re
import wave
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .config import make_stdio_encoding_safe, resolve_workspace_override

# OCR'd docs land in the SAME shard the directory ingest (gap I1) uses, so the
# corpus has one "ingested content" front door. ``docs/cortex-*`` is exactly
# what ``CortexSearchIndex._iter_document_paths`` globs.
OCR_SHARD = "cortex-ingest"
QUARANTINE_REL = "ocr-quarantine.jsonl"  # under library/cortex-library/
_MANIFEST_DIR = ("library", "cortex-library")
_SLUG_MAX_LEN = 80

# A common OCR-target set. Kept small and explicit; an unknown suffix is still
# attempted (the backend decides what it can open) -- this is only used for a
# friendlier CLI error and MIME guessing, never to gate real extraction.
IMAGE_EXTENSIONS = frozenset(
    {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".gif", ".webp", ".ppm", ".pgm"}
)

# Strongest-first preference order for the pluggable OCR backend. select_ocr_backend
# walks this list and picks the first AVAILABLE backend (unless a caller forces one).
OCR_BACKEND_ORDER = ("vlm", "paddleocr", "tesseract")


@dataclass(frozen=True)
class OcrResult:
    status: str  # "ok" | "quarantined"
    text: str | None
    engine: str | None  # human-readable engine label (e.g. "vlm:qwen-vl", "tesseract 5.3")
    reason: str | None
    backend: str | None = None  # which backend was chosen ("vlm"|"paddleocr"|"tesseract")


@dataclass(frozen=True)
class TtsResult:
    status: str  # "ok" | "quarantined"
    wav_path: str | None
    engine: str | None
    reason: str | None
    duration_s: float | None


# --------------------------------------------------------------------------- #
# VLM / LLM OCR backend -- reuses judge.py's API-tier .env config pattern
# --------------------------------------------------------------------------- #
# Recorded corpus decision (cortex_core/judge.py MIN_MAX_TOKENS_BY_TIER + CLAUDE.md
# research-first note): OpenAI-compatible reasoning endpoints silently return
# content="" / finish_reason="length" below a 12000 max_tokens floor. A VLM
# transcribing a dense page also legitimately needs a generous ceiling, so this
# floor is both a correctness guard and a sensible default. max_tokens is a CEILING,
# not a reservation -- generation still stops at the real end-of-answer, so a high
# floor costs nothing on short pages.
VLM_OCR_MIN_MAX_TOKENS = 12000

# The VLM is instructed to emit this exact sentinel when the image has NO readable
# text, so an empty/blank page QUARANTINES honestly instead of the model inventing
# plausible-looking words -- the cardinal anti-fabrication rule, applied to the VLM.
_VLM_NO_TEXT_SENTINEL = "<<<NO_TEXT>>>"

_VLM_SYSTEM_PROMPT = (
    "You are a precise OCR engine. Transcribe the text visible in the image EXACTLY "
    "as it appears -- verbatim, preserving reading order and line breaks. Do NOT "
    "translate, summarize, correct, explain, or add any commentary, headings, labels, "
    "or code fences. Output ONLY the transcribed text. If the image contains NO "
    "readable text, output exactly this token and nothing else: " + _VLM_NO_TEXT_SENTINEL
)

_MIME_BY_SUFFIX = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".bmp": "image/bmp",
    ".webp": "image/webp",
    ".tif": "image/tiff",
    ".tiff": "image/tiff",
}


def _guess_image_mime(path: str | Path) -> str:
    return _MIME_BY_SUFFIX.get(Path(path).suffix.lower(), "image/png")


def _strip_code_fences(text: str) -> str:
    """Remove a single wrapping ```...``` fence a chatty VLM may add around the
    transcription. Only strips a fence that wraps the WHOLE output -- never touches
    a code block that is genuinely part of the recognized page."""
    t = text.strip()
    if t.startswith("```") and t.endswith("```") and t.count("```") == 2:
        inner = t[3:-3]
        # drop an optional language tag on the opening fence's first line
        if "\n" in inner:
            first, rest = inner.split("\n", 1)
            if first.strip().isalpha():
                return rest.strip()
        return inner.strip()
    return t


def vlm_ocr_config(env: dict[str, str] | None = None) -> tuple[str, str, str] | None:
    """Resolve ``(url, key, model)`` for the VLM OCR endpoint from ``.env`` -- reusing
    ``judge.load_env`` (the same tiny parser / precedence the judge tiers use). Returns
    ``None`` when not fully configured (backend then reports itself unavailable)."""
    from .judge import load_env

    env = env if env is not None else load_env()
    url = env.get("VLM_OCR_API_URL", "").strip()
    key = env.get("VLM_OCR_API_KEY", "").strip()
    model = env.get("VLM_OCR_MODEL", "").strip()
    if url and key and model:
        return url, key, model
    return None


def _vlm_probe(env: dict[str, str] | None = None) -> tuple[bool, str | None]:
    """``(available, label_or_reason)``. Available == fully configured (url+key+model).
    Like Tesseract's local version probe, this does NOT make a network call; a config
    that is present but unreachable surfaces as a quarantine at recognition time."""
    try:
        import httpx  # noqa: F401
    except Exception:
        return False, "httpx_not_installed"
    cfg = vlm_ocr_config(env)
    if cfg is None:
        return False, "vlm_ocr_not_configured"
    return True, f"vlm:{cfg[2]}"


def _ocr_vlm(
    path: str | Path,
    env: dict[str, str] | None = None,
    *,
    http_post=None,
    timeout: float = 120.0,
    max_tokens: int = VLM_OCR_MIN_MAX_TOKENS,
) -> OcrResult:
    """OCR one image with a vision model over an OpenAI-compatible endpoint. Any missing
    config / transport failure / empty-or-sentinel response -> quarantine, NEVER invented
    text. ``http_post`` is a test injection seam (callable(url, headers=, json=) -> resp
    with ``.json()`` / ``.raise_for_status()``), mirroring ``judge.llm_judge``."""
    cfg = vlm_ocr_config(env)
    if cfg is None:
        return OcrResult("quarantined", None, None, "vlm_ocr_not_configured", backend="vlm")
    url, key, model = cfg
    try:
        import httpx

        from .judge import _chat_completions_url, _extract_content
    except Exception as exc:  # noqa: BLE001
        return OcrResult(
            "quarantined", None, f"vlm:{model}", f"vlm_import_failed:{type(exc).__name__}",
            backend="vlm",
        )

    # Honor the recorded 12000 max_tokens floor (judge.MIN_MAX_TOKENS_BY_TIER): below it,
    # reasoning endpoints silently return empty content.
    max_tokens = max(int(max_tokens), VLM_OCR_MIN_MAX_TOKENS)
    try:
        raw = Path(path).read_bytes()
    except OSError as exc:
        return OcrResult(
            "quarantined", None, f"vlm:{model}", f"read_failed:{type(exc).__name__}",
            backend="vlm",
        )
    data_uri = f"data:{_guess_image_mime(path)};base64,{base64.b64encode(raw).decode('ascii')}"
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": _VLM_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Transcribe all text in this image verbatim."},
                    {"type": "image_url", "image_url": {"url": data_uri}},
                ],
            },
        ],
        "temperature": 0.0,
        "max_tokens": max_tokens,
        "stream": False,
    }
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    endpoint = _chat_completions_url(url)
    try:
        if http_post is not None:
            resp = http_post(endpoint, headers=headers, json=payload)
        else:
            resp = httpx.post(endpoint, headers=headers, json=payload, timeout=timeout)
        resp.raise_for_status()
        content = _extract_content(resp.json())
    except Exception as exc:  # noqa: BLE001 -- any transport/shape error -> honest quarantine
        return OcrResult(
            "quarantined", None, f"vlm:{model}", f"vlm_call_failed:{type(exc).__name__}",
            backend="vlm",
        )
    text = _strip_code_fences((content or "").strip())
    if not text or _VLM_NO_TEXT_SENTINEL in text:
        # Model saw nothing readable (or refused) -- quarantine, do NOT emit invented text.
        return OcrResult("quarantined", None, f"vlm:{model}", "no_text_detected", backend="vlm")
    return OcrResult("ok", text, f"vlm:{model}", None, backend="vlm")


# --------------------------------------------------------------------------- #
# PaddleOCR backend -- strong local neural OCR (optional)
# --------------------------------------------------------------------------- #
_PADDLE_ENGINE = None  # cached; constructing PaddleOCR is expensive (model load)


def _paddle_probe(env: dict[str, str] | None = None) -> tuple[bool, str | None]:
    """``(available, label_or_reason)``. Importable ``paddleocr`` == available."""
    try:
        import paddleocr  # noqa: F401
    except Exception:
        return False, "paddleocr_not_installed"
    return True, "paddleocr"


def _get_paddle_engine():
    global _PADDLE_ENGINE
    if _PADDLE_ENGINE is None:
        from paddleocr import PaddleOCR

        _PADDLE_ENGINE = PaddleOCR(use_angle_cls=True, lang="en", show_log=False)
    return _PADDLE_ENGINE


def _ocr_paddle(path: str | Path, env: dict[str, str] | None = None) -> OcrResult:
    """OCR one image with PaddleOCR. Missing engine / failure / empty recognition ->
    quarantine with ``text=None`` -- never invented text."""
    available, reason = _paddle_probe(env)
    if not available:
        return OcrResult("quarantined", None, None, reason, backend="paddleocr")
    try:
        engine = _get_paddle_engine()
        result = engine.ocr(str(path), cls=True)
    except Exception as exc:  # noqa: BLE001
        return OcrResult(
            "quarantined", None, "paddleocr", f"ocr_failed:{type(exc).__name__}",
            backend="paddleocr",
        )
    # result shape: [ per-image [ [box, (text, confidence)], ... ] ]
    lines: list[str] = []
    for page in result or []:
        for entry in page or []:
            try:
                txt = entry[1][0]
            except (IndexError, TypeError):
                continue
            if txt:
                lines.append(str(txt))
    text = "\n".join(lines).strip()
    if not text:
        return OcrResult("quarantined", None, "paddleocr", "no_text_detected", backend="paddleocr")
    return OcrResult("ok", text, "paddleocr", None, backend="paddleocr")


# --------------------------------------------------------------------------- #
# Tesseract backend -- the last-resort floor (existing engine)
# --------------------------------------------------------------------------- #
def _tesseract_probe(env: dict[str, str] | None = None) -> tuple[bool, str | None]:
    """``(available, label_or_reason)``. Importing ``pytesseract`` is NOT sufficient --
    the ``tesseract`` binary must be on PATH. We probe it for real."""
    try:
        import pytesseract  # noqa: F401
    except Exception:
        return False, "pytesseract_not_installed"
    try:
        from PIL import Image  # noqa: F401
    except Exception:
        return False, "pillow_not_installed"
    try:
        version = str(pytesseract.get_tesseract_version())
    except Exception:
        return False, "tesseract_binary_missing"
    return True, f"tesseract {version}"


def _ocr_tesseract(path: str | Path, env: dict[str, str] | None = None) -> OcrResult:
    """OCR one image with Tesseract. Missing engine / failure / empty recognition ->
    quarantine with ``text=None`` -- never invented text."""
    available, label = _tesseract_probe(env)
    if not available:
        return OcrResult("quarantined", None, None, label, backend="tesseract")
    engine_label = label  # "tesseract <version>"
    try:
        import pytesseract
        from PIL import Image

        with Image.open(path) as img:
            text = pytesseract.image_to_string(img)
    except Exception as exc:  # noqa: BLE001
        return OcrResult(
            "quarantined", None, None, f"ocr_failed:{type(exc).__name__}", backend="tesseract"
        )
    text = (text or "").strip()
    if not text:
        # Legitimately no recognizable text -- quarantine, do NOT emit "".
        return OcrResult("quarantined", None, engine_label, "no_text_detected", backend="tesseract")
    return OcrResult("ok", text, engine_label, None, backend="tesseract")


# --------------------------------------------------------------------------- #
# Backend selection -- the pluggable, honest real-vs-stub gate
# --------------------------------------------------------------------------- #
def _probe_for(name: str):
    # Built fresh each call so tests can monkeypatch a backend's probe by module attr.
    return {
        "vlm": _vlm_probe,
        "paddleocr": _paddle_probe,
        "tesseract": _tesseract_probe,
    }[name]


def _recognizer_for(name: str):
    return {
        "vlm": _ocr_vlm,
        "paddleocr": _ocr_paddle,
        "tesseract": _ocr_tesseract,
    }[name]


def _preference_order(prefer: str | None) -> list[str]:
    if not prefer:
        return list(OCR_BACKEND_ORDER)
    prefer = prefer.lower().strip()
    if prefer not in OCR_BACKEND_ORDER:
        raise ValueError(f"unknown OCR backend {prefer!r}; known: {sorted(OCR_BACKEND_ORDER)}")
    return [prefer]  # a forced backend is used alone (no silent fallback)


def select_ocr_backend(
    prefer: str | None = None, env: dict[str, str] | None = None
) -> tuple[str | None, str | None, dict[str, str | None]]:
    """Pick the strongest AVAILABLE OCR backend in VLM -> PaddleOCR -> Tesseract order.

    Returns ``(backend_name, engine_label, reasons)``. ``backend_name`` is ``None`` when
    none is available (``reasons`` then maps each tried backend to why it was skipped).
    ``prefer`` forces a single backend (no fallback) -- useful for A/B and for the CLI."""
    reasons: dict[str, str | None] = {}
    for name in _preference_order(prefer):
        available, detail = _probe_for(name)(env)
        if available:
            return name, detail, reasons
        reasons[name] = detail
    return None, None, reasons


def ocr_backends_status(env: dict[str, str] | None = None) -> dict[str, dict]:
    """Availability of every backend (for ``cortex-ocr --engines`` / diagnostics)."""
    out: dict[str, dict] = {}
    for name in OCR_BACKEND_ORDER:
        available, detail = _probe_for(name)(env)
        out[name] = {"available": available, "detail": detail}
    return out


def ocr_engine_status(env: dict[str, str] | None = None) -> tuple[bool, str | None]:
    """Aggregate: ``(available, reason_if_not)`` -- True if ANY OCR backend is available.

    Kept for backward-compat (the CLI / callers / tests use it as the top-level gate).
    When unavailable, the reason names every backend and why it was skipped."""
    name, _label, reasons = select_ocr_backend(env=env)
    if name is not None:
        return True, None
    reason = "no_ocr_backend:" + ";".join(f"{k}={v}" for k, v in reasons.items())
    return False, reason


# --------------------------------------------------------------------------- #
# OCR -- never fabricates
# --------------------------------------------------------------------------- #
def ocr_image(
    path: str | Path,
    *,
    prefer: str | None = None,
    env: dict[str, str] | None = None,
    http_post=None,
) -> OcrResult:
    """OCR one image via the pluggable backend (VLM -> PaddleOCR -> Tesseract by
    availability, or a forced ``prefer`` backend). On any missing backend / failure /
    empty recognition, return ``status="quarantined"`` with ``text=None`` -- NEVER
    invented text. The chosen ``backend`` and ``engine`` are recorded on the result."""
    path = Path(path)
    # Backward-compatible honest gate: when a caller/test forces "no engine" via
    # ocr_engine_status, respect it. (Only for the auto-select path; a forced `prefer`
    # backend runs its own probe.)
    if prefer is None:
        available, reason = ocr_engine_status()
        if not available:
            return OcrResult("quarantined", None, None, reason, backend=None)
    backend, _label, reasons = select_ocr_backend(prefer=prefer, env=env)
    if backend is None:
        reason = "no_ocr_backend:" + ";".join(f"{k}={v}" for k, v in reasons.items())
        return OcrResult("quarantined", None, None, reason, backend=None)
    recognizer = _recognizer_for(backend)
    if backend == "vlm":
        return recognizer(path, env=env, http_post=http_post)
    return recognizer(path, env=env)


# --------------------------------------------------------------------------- #
# TTS -- real audio or an honest no-op
# --------------------------------------------------------------------------- #
def tts_engine_status() -> tuple[bool, str | None]:
    """Return ``(available, reason_if_not)`` for offline TTS via pyttsx3."""
    try:
        import pyttsx3
    except Exception:
        return False, "pyttsx3_not_installed"
    try:
        engine = pyttsx3.init()
        engine.stop()
    except Exception as exc:  # no speech driver (headless Linux w/o espeak, ...)
        return False, f"no_tts_driver:{type(exc).__name__}"
    return True, None


def synthesize_speech(text: str, out_wav: str | Path) -> TtsResult:
    """Render ``text`` to a WAV file. Quarantines (writes no file) when no
    engine is available -- never an empty/fake wav."""
    out_wav = Path(out_wav)
    available, reason = tts_engine_status()
    if not available:
        return TtsResult("quarantined", None, None, reason, None)
    if not (text or "").strip():
        return TtsResult("quarantined", None, "pyttsx3", "empty_text", None)
    try:
        import pyttsx3

        out_wav.parent.mkdir(parents=True, exist_ok=True)
        engine = pyttsx3.init()
        engine.save_to_file(text, str(out_wav))
        engine.runAndWait()
        engine.stop()
    except Exception as exc:
        return TtsResult("quarantined", None, "pyttsx3", f"tts_failed:{type(exc).__name__}", None)
    if not out_wav.exists() or out_wav.stat().st_size <= 44:  # 44 = bare WAV header
        return TtsResult("quarantined", None, "pyttsx3", "no_audio_written", None)
    duration = None
    try:
        with wave.open(str(out_wav)) as w:
            frames, rate = w.getnframes(), w.getframerate()
            duration = frames / rate if rate else None
    except Exception:
        pass
    return TtsResult("ok", str(out_wav), "pyttsx3", None, duration)


def timing_map(text: str, duration_s: float) -> dict:
    """A word->time map spread proportionally to word length across the measured
    clip duration. Honestly labeled ``proportional_estimate`` -- this is NOT
    forced alignment (pyttsx3 exposes no phoneme timings), just a useful
    read-along approximation. Never claims measurement it did not do."""
    words = [w for w in re.split(r"\s+", (text or "").strip()) if w]
    if not words or not duration_s or duration_s <= 0:
        return {"method": "proportional_estimate", "duration_s": duration_s, "words": []}
    weights = [len(w) for w in words]
    total = sum(weights) or len(words)
    out = []
    cursor = 0.0
    for i, (w, wt) in enumerate(zip(words, weights, strict=True)):
        span = duration_s * (wt / total)
        start = cursor
        end = duration_s if i == len(words) - 1 else cursor + span
        out.append({"word": w, "start": round(start, 4), "end": round(end, 4)})
        cursor = end
    return {"method": "proportional_estimate", "duration_s": duration_s, "words": out}


# --------------------------------------------------------------------------- #
# Corpus materialization -- reuses the ingest shard/index (gap I1)
# --------------------------------------------------------------------------- #
def _slug(source: str) -> str:
    stem = Path(source).stem or "scan"
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", stem.lower()).strip("-")
    return (slug[:_SLUG_MAX_LEN].strip("-")) or "scan"


def _manifest_path(ws: Path) -> Path:
    p = ws
    for part in _MANIFEST_DIR:
        p = p / part
    return p / "ocr-ingest-manifest.json"


def _load_manifest(path: Path) -> dict:
    if path.exists():
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
    tmp.replace(path)  # atomic -> resumable


def materialize_ocr_doc(
    text: str,
    source: str,
    workspace: str | Path | None = None,
    *,
    engine: str | None = None,
    backend: str | None = None,
    reindex: bool = True,
) -> dict:
    """Write OCR'd ``text`` as a corpus doc under ``docs/cortex-ingest/`` with OCR
    provenance in the frontmatter (``ocr_engine`` label + ``ocr_backend`` name), then
    (by default) rebuild the search index so it is immediately searchable. Idempotent:
    same source+content -> the same single file. Refuses to write empty/whitespace text
    (no theater)."""
    if not (text or "").strip():
        raise ValueError("refusing to materialize empty OCR text")
    ws = resolve_workspace_override(workspace)
    out_dir = ws / "docs" / OCR_SHARD
    out_dir.mkdir(parents=True, exist_ok=True)

    content_hash = hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()
    manifest_path = _manifest_path(ws)
    manifest = _load_manifest(manifest_path)
    entries: dict = manifest["entries"]
    source_key = str(source)

    # Reuse the same output file for an unchanged source; overwrite on change.
    out_rel = None
    for rel, meta in entries.items():
        if meta.get("source") == source_key:
            out_rel = rel
            break
    if out_rel is None:
        out_rel = f"ocr-{_slug(source)}-{content_hash[:8]}.md"
        suffix = 1
        while out_rel in entries and entries[out_rel].get("hash") != content_hash:
            out_rel = f"ocr-{_slug(source)}-{content_hash[:8]}-{suffix}.md"
            suffix += 1

    now = datetime.now(timezone.utc).isoformat()
    doc = (
        "---\n"
        f"source_path: {json.dumps(source_key)}\n"
        f"content_hash: {json.dumps(content_hash)}\n"
        f"ocr_engine: {json.dumps(engine or 'unknown')}\n"
        f"ocr_backend: {json.dumps(backend or 'unknown')}\n"
        "ocr_status: ok\n"
        f"ingested_at: {json.dumps(now)}\n"
        "---\n\n"
        + text.strip()
        + "\n"
    )
    (out_dir / out_rel).write_text(doc, encoding="utf-8")
    entries[out_rel] = {
        "source": source_key,
        "hash": content_hash,
        "engine": engine,
        "backend": backend,
    }
    _save_manifest(manifest_path, manifest)

    if reindex:
        from .search import CortexSearchIndex

        CortexSearchIndex(ws).rebuild()

    return {"corpus_doc": str(out_dir / out_rel), "content_hash": content_hash, "shard": OCR_SHARD}


def _quarantine(ws: Path, record: dict) -> Path:
    """Append a quarantine record. This is the anti-fabrication audit trail:
    a page we could NOT honestly OCR is recorded here, never guessed into the
    corpus."""
    path = ws
    for part in _MANIFEST_DIR:
        path = path / part
    path = path / QUARANTINE_REL
    path.parent.mkdir(parents=True, exist_ok=True)
    record = {"quarantined_at": datetime.now(timezone.utc).isoformat(), **record}
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, sort_keys=True) + "\n")
    return path


# --------------------------------------------------------------------------- #
# Orchestration: image -> corpus (+ optional speech)
# --------------------------------------------------------------------------- #
def ingest_image(
    image_path: str | Path,
    workspace: str | Path | None = None,
    *,
    prefer: str | None = None,
    env: dict[str, str] | None = None,
    http_post=None,
    tts_out: str | Path | None = None,
    reindex: bool = True,
) -> dict:
    """OCR one image and, on success, materialize it into the corpus. On any
    quarantine, record it (with the chosen backend) and write NO corpus doc.
    Optionally also synthesize speech (+ a labeled timing map) from the recognized
    text. ``prefer`` forces a specific OCR backend."""
    image_path = Path(image_path)
    ws = resolve_workspace_override(workspace)
    ocr = ocr_image(image_path, prefer=prefer, env=env, http_post=http_post)

    if ocr.status != "ok" or not ocr.text:
        q = _quarantine(
            ws,
            {
                "source": str(image_path.resolve()),
                "stage": "ocr",
                "backend": ocr.backend,
                "engine": ocr.engine,
                "reason": ocr.reason,
            },
        )
        return {
            "status": "quarantined",
            "reason": ocr.reason,
            "backend": ocr.backend,
            "engine": ocr.engine,
            "corpus_doc": None,
            "quarantine_log": str(q),
        }

    mat = materialize_ocr_doc(
        ocr.text,
        source=str(image_path.resolve()),
        workspace=ws,
        engine=ocr.engine,
        backend=ocr.backend,
        reindex=reindex,
    )
    result = {
        "status": "ok",
        "backend": ocr.backend,
        "engine": ocr.engine,
        "chars": len(ocr.text),
        "corpus_doc": mat["corpus_doc"],
        "content_hash": mat["content_hash"],
    }

    if tts_out is not None:
        tts = synthesize_speech(ocr.text, tts_out)
        result["tts"] = {
            "status": tts.status,
            "wav_path": tts.wav_path,
            "engine": tts.engine,
            "reason": tts.reason,
            "duration_s": tts.duration_s,
        }
        if tts.status == "ok" and tts.duration_s:
            result["timing_map"] = timing_map(ocr.text, tts.duration_s)
    return result


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def main(argv: list[str] | None = None) -> int:
    make_stdio_encoding_safe()
    parser = argparse.ArgumentParser(
        prog="cortex-ocr",
        description=(
            "OCR a scanned page/image into the corpus and optionally read it aloud "
            "(gap I2, the Kurzweil milestone). Pluggable OCR backend (VLM -> PaddleOCR -> "
            "Tesseract by availability). Never fabricates text: a page with no available "
            "OCR backend is quarantined, not invented."
        ),
    )
    parser.add_argument("image", nargs="?", help="path to an image to OCR")
    parser.add_argument("--workspace", default=None)
    parser.add_argument(
        "--backend",
        choices=sorted(OCR_BACKEND_ORDER),
        default=None,
        help="force a specific OCR backend (default: strongest available, VLM>Paddle>Tesseract)",
    )
    parser.add_argument(
        "--ingest",
        action="store_true",
        help="materialize the OCR'd text into the corpus (default: just print it)",
    )
    parser.add_argument("--tts", metavar="OUT.wav", default=None, help="also synthesize speech to a WAV")
    parser.add_argument(
        "--no-reindex", action="store_true", help="skip the index rebuild on --ingest"
    )
    parser.add_argument(
        "--engines", action="store_true", help="report OCR/TTS backend availability and exit"
    )
    args = parser.parse_args(argv)

    if args.engines:
        tts_ok, tts_reason = tts_engine_status()
        selected, sel_label, _reasons = select_ocr_backend()
        print(json.dumps(
            {
                "ocr": {
                    "selected": selected,
                    "selected_engine": sel_label,
                    "backends": ocr_backends_status(),
                },
                "tts": {"available": tts_ok, "reason": tts_reason},
            },
            indent=2,
        ))
        return 0

    if not args.image:
        parser.error("an image path is required (or use --engines)")
    image = Path(args.image).expanduser()
    if not image.is_file():
        print(f"error: not a file: {image}")
        return 2

    if args.ingest or args.tts:
        result = ingest_image(
            image,
            workspace=args.workspace,
            prefer=args.backend,
            tts_out=args.tts,
            reindex=not args.no_reindex,
        )
    else:
        ocr = ocr_image(image, prefer=args.backend)
        result = {
            "status": ocr.status,
            "backend": ocr.backend,
            "engine": ocr.engine,
            "reason": ocr.reason,
            "text": ocr.text,
        }
    print(json.dumps(result, indent=2))
    # A quarantine is an honest outcome, not a crash: exit 0, but signal the
    # non-extraction via the printed status.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
