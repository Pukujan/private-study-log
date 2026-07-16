# OCR + TTS ingest lane (gap I2 — the "Kurzweil 3000" milestone)

Date: 2026-07-14 · Module: `cortex_core/ocr_tts.py` · CLI: `cortex-ocr` ·
Optional extra: `pip install -e .[ocr]`

## Why

The collaborator's use case needs the accessibility front door Kurzweil 3000
gives: turn a **scanned page / image → corpus text** (OCR), and **text →
speech** (TTS). Prior ingest paths only handle already-digital text:
`cortex-fetch` (single URL, HTML→text) and `cortex-ingest` (bulk directory
crawl, gap I1). Neither can read an image. A first attempt at this lane was
lost in a session restart; this is the clean rebuild.

## Research-first (what already existed — cited)

- **Intended shape** comes from the pre-existing A/B/C manifest
  `evals/ab_cortex_scaffold/runs/20260713T232840Z-kurzweil_ocr_tts/` (in git
  history; commit `de6d79d`). Its task spec: *"OCR it, read the text aloud
  (produce audio), and emit a structured study note + a word-timing map …
  outputs: ocr_text.txt, audio.wav, timing_map.json, note.json"*. That run was
  a `StubAgentInvoker` scaffold placeholder (all metrics 0.0, "do not ship") —
  a **spec, not an implementation**. This module is the real implementation.
- **Ingest convention reused** — `cortex_core/ingest.py` (gap I1, on branch
  `claude/corpus-agent-orchestration-review-121x1q`) materializes each doc as a
  `.md` under `docs/cortex-ingest/` with YAML frontmatter
  (`source_path`/`content_hash`/`ingested_at`), dedupes by SHA-256, keeps an
  atomic manifest, and calls `CortexSearchIndex(ws).rebuild()`. This lane
  **writes into the same `docs/cortex-ingest/` shard** (the index globs
  `docs/cortex-*` and rglobs `*.md` — verified in `cortex_core/search.py`
  `_iter_document_paths`), so OCR'd text flows into the exact corpus path I1
  built. It reimplements no retrieval.
- **No prior OCR/TTS engine decision existed** in the corpus/closeouts —
  `ingest.py::_extract_pdf` explicitly defers scanned/image PDFs to *"OCR (gap
  I2), out of scope here"*. So engine choice was open; picked the lightest
  offline options (below).

## Engine choices — OCR is now a PLUGGABLE backend (2026-07-14 upgrade)

The original lane was Tesseract-only. The Kurzweil-3000 milestone deserves real
OCR, so the OCR path is now **pluggable** with a strongest-first preference order.
`select_ocr_backend(prefer=None, env=None)` walks the order and picks the first
**available** backend; every backend is OPTIONAL and honestly probed. TTS is
unchanged.

| Order | Backend | Engine | Probe (the real gate) | Dep |
|---|---|---|---|---|
| 1 | `vlm` | VLM/LLM OCR — vision model (e.g. Qwen-VL) over an OpenAI-compatible `/chat/completions` endpoint | `VLM_OCR_API_URL`/`_KEY`/`_MODEL` all set in `.env` (config-present; no network probe — a config that is present but unreachable surfaces as a quarantine at recognition time) | none (reuses `httpx`, already core) |
| 2 | `paddleocr` | Strong LOCAL neural OCR | `import paddleocr` succeeds | `[ocr-paddle]` |
| 3 | `tesseract` | `pytesseract` + `Pillow` → system `tesseract` binary — the last-resort floor | `pytesseract.get_tesseract_version()` — importing the wrapper is **not** enough; the binary must be on PATH | `[ocr]` |
| TTS | — | `pyttsx3` → OS speech (SAPI5 / NSSpeech / espeak) | `pyttsx3.init()` succeeds | `[ocr]` |

**VLM backend (the headline upgrade).** Reuses `judge.py`'s API-tier config
pattern verbatim: `judge.load_env` (the tiny `.env` parser), `_chat_completions_url`
(endpoint normalization), and `_extract_content` (pull assistant text, incl.
`reasoning_content` fallback). The image is base64-encoded into a
`data:<mime>;base64,…` URI and sent as an OpenAI-compatible `image_url` content
part. It **respects the recorded 12000 `max_tokens` floor** (`judge.MIN_MAX_TOKENS_BY_TIER`
— below it, reasoning endpoints silently return `content=""`/`finish_reason="length"`;
`VLM_OCR_MIN_MAX_TOKENS = 12000`, clamped up, never down). Strongest on messy/
low-quality scans. Point `VLM_OCR_*` at any OpenAI-compatible vision endpoint the
collaborator has (OpenRouter / 9router Qwen-VL, etc.) — no extra install.

**Forcing a backend.** `prefer=` / `cortex-ocr --backend {vlm,paddleocr,tesseract}`
forces a single backend with **no silent fallback** (for A/B and diagnostics).

Backends stay out of core `dependencies`: `[ocr]` (Tesseract wrapper + Pillow +
pyttsx3) and `[ocr-paddle]` (PaddleOCR + paddlepaddle) optional groups mirror the
existing `vector`/`browser` pattern. Core stays dependency-light and the lane
degrades gracefully — to the next backend, then to an honest quarantine — when a
backend isn't present.

## The cardinal rule extends to the VLM: NEVER fabricate

A VLM is generative, so the anti-fabrication rule needs teeth here: the system
prompt orders **verbatim transcription only** (no translation/summary/commentary/
fences) and a `<<<NO_TEXT>>>` sentinel for a blank page. On the sentinel, an empty
response, or any transport failure → `status="quarantined"`, `text=None` — the
lane never invents words, and an accidental wrapping code-fence is stripped, never
corrupted. The chosen engine is recorded per page (`OcrResult.backend`, the doc's
`ocr_engine`/`ocr_backend` frontmatter, and the quarantine record's `backend`) —
full provenance for which backend read (or failed to read) each page.

## The cardinal rule: OCR NEVER fabricates

Evidence-theater is the disqualifying failure here — a fabricated "extraction"
poisons the very corpus Cortex exists to keep trustworthy. So:

- No engine, engine failure, or **empty recognition** → `status="quarantined"`,
  `text=None`, a record appended to
  `library/cortex-library/ocr-quarantine.jsonl`, and **no doc enters the
  corpus**. `ingest_image` returns `corpus_doc: null`.
- `materialize_ocr_doc` **raises** on empty/whitespace text — there is no code
  path that writes an invented or blank OCR doc.
- TTS similarly writes **no fake/empty wav** when absent (quarantines); it also
  rejects a wav ≤ 44 bytes (bare header) as `no_audio_written`.
- The word-timing map is honestly labeled `method: "proportional_estimate"`
  (spread by word length across the *measured* clip duration) — it does **not**
  claim forced alignment, which pyttsx3 cannot provide.

## Public API

`ocr_engine_status(env=None)` (aggregate: any backend available) /
`tts_engine_status()` → `(available, reason)` ·
`ocr_backends_status(env=None)` (per-backend availability) ·
`select_ocr_backend(prefer=None, env=None) → (backend, engine_label, skip_reasons)` ·
`ocr_image(path, *, prefer=None, env=None, http_post=None) → OcrResult` (now carries
`.backend`) · `synthesize_speech(text, wav) → TtsResult` · `timing_map(text, dur)` ·
`materialize_ocr_doc(text, source, ws, *, engine=…, backend=…, …)` ·
`ingest_image(image, ws, *, prefer=…, env=…, http_post=…, tts_out=…, reindex=…)`.
`http_post` is a test injection seam (mirrors `judge.llm_judge`) — no network in tests.

CLI: `cortex-ocr IMAGE [--backend {vlm,paddleocr,tesseract}] [--ingest] [--tts OUT.wav]
[--workspace …] [--no-reindex]`, plus `cortex-ocr --engines` (report per-backend
availability + which is selected). **CLI-only — no new MCP tool** (anti-bloat honored).

## Tests (TDD, `tests/test_ocr_tts.py` — 20 tests, 18 pass / 2 engine-skips)

(a) **backend selection** prefers VLM→Paddle→Tesseract by availability, falls back
in order, records skip reasons, and honors a forced `prefer` with no silent
fallback; (b) a **real image OCRs when a backend is present** — the VLM path proven
end-to-end via an injected transport (deterministic, no network; asserts the image
is sent, the endpoint is `/chat/completions`, and the 12000 floor is honored) plus
the real-Tesseract path (skips when the binary is absent); (c) **no-backend still
quarantines honestly** — `text=None`, nothing enters the corpus, quarantine record
written; (d) the **chosen engine is recorded** — `.backend`/`.engine` on the result,
`ocr_engine`/`ocr_backend` frontmatter, and the quarantine record's `backend`. Plus
the original idempotent re-materialize, real-vs-absent TTS, and labeled-estimate
timing-map tests. Engine-dependent tests skip honestly rather than fake a pass.

## Real-vs-stub on the build machine (2026-07-14, pluggable upgrade)

- **All three OCR backends unavailable here (honest graceful degradation).**
  `vlm` → `vlm_ocr_not_configured` (no `VLM_OCR_*` in `.env` on this machine);
  `paddleocr` → `paddleocr_not_installed`; `tesseract` → `tesseract_binary_missing`
  (wrapper + Pillow import, binary not on PATH). So live OCR degrades to an honest
  quarantine everywhere — verified by the passing no-fabrication tests.
- **VLM path proven end-to-end via injected transport (real code, stub network).**
  The request shape (base64 `image_url`, `/chat/completions`, 12000-floor `max_tokens`),
  the verbatim-transcription contract, the `<<<NO_TEXT>>>`→quarantine rule, transport-
  failure→quarantine, and provenance recording are all exercised deterministically.
  To make it REAL here: set `VLM_OCR_API_URL`/`_KEY`/`_MODEL` in `.env` to an
  OpenAI-compatible vision endpoint (the collaborator's OpenRouter/9router Qwen-VL).
- **TTS = REAL.** `pyttsx3` via Windows SAPI5 (unchanged from the baseline).

## Honest debt / limits

- No live OCR ran on this machine (no configured VLM, no PaddleOCR, no Tesseract
  binary) — the VLM lane is proven by injected transport + design, not a live call;
  a real recognition awaits a configured `VLM_OCR_*`, a PaddleOCR install, or a
  Tesseract binary (CI runner). The stub-vs-real boundary is honestly labeled.
- PaddleOCR probe is import-only (does not construct the model at probe time, which
  is expensive); a broken install could still fail at recognition time → quarantine.
- Timing map is an estimate, not forced alignment (labeled as such).
- No multi-page PDF→image splitting yet (single image per call); layout/columns
  are whatever the chosen backend returns.
- Deliberately did **not** merge the gap-I1 `ingest.py` into this branch (it
  lives on `claude/corpus-agent-orchestration-review-121x1q`); this lane reuses
  I1's *shard/index convention* directly so it stands alone and reversible. When
  the branches converge, both feed one `docs/cortex-ingest/` shard.
