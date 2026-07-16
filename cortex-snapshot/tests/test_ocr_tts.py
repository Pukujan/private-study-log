"""Tests for the OCR + TTS ingest lane (gap I2, the "Kurzweil 3000" milestone).

Cardinal rule under test: the OCR path must NEVER fabricate text. When no OCR
engine is available it quarantines and writes NOTHING into the corpus -- it does
not invent content. TTS is real when an engine is present, a labeled stub when
not. Engine-dependent assertions are conditional (skip when the engine binary is
absent on this machine) so the suite is honest about real-vs-stub everywhere.
"""
from __future__ import annotations

import json
import sys
import wave
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cortex_core import ocr_tts  # noqa: E402


# --------------------------------------------------------------------------- #
# Fixtures / helpers
# --------------------------------------------------------------------------- #
def _make_text_image(path: Path, text: str) -> bool:
    """Render `text` onto a white PNG. Returns False (skip) if PIL is absent."""
    try:
        from PIL import Image, ImageDraw
    except Exception:
        return False
    img = Image.new("RGB", (480, 90), "white")
    draw = ImageDraw.Draw(img)
    # A larger built-in font renders more legibly for the OCR engine.
    try:
        from PIL import ImageFont

        font = ImageFont.load_default(size=34)
    except Exception:
        font = None
    draw.text((10, 25), text, fill="black", font=font)
    img.save(path)
    return True


def _make_workspace(tmp_path: Path) -> Path:
    """A minimal but real Cortex checkout so workspace resolution succeeds
    (mirrors tests/test_ingest.py)."""
    workspace = tmp_path / "workspace"
    (workspace / "library" / "cortex-library" / "search").mkdir(parents=True)
    (workspace / "cortex.json").write_text(
        json.dumps({"paths": {"workspace_fallback": ""}}), encoding="utf-8"
    )
    return workspace


def _shard_docs(workspace: Path) -> list[Path]:
    d = workspace / "docs" / ocr_tts.OCR_SHARD
    return sorted(d.glob("*.md")) if d.exists() else []


# --------------------------------------------------------------------------- #
# (b) Graceful, LABELED degradation -- no fabricated text
# --------------------------------------------------------------------------- #
def test_ocr_quarantines_without_engine_and_never_fabricates(monkeypatch, tmp_path):
    monkeypatch.setattr(ocr_tts, "ocr_engine_status", lambda: (False, "forced_absent"))
    img = tmp_path / "scan.png"
    img.write_bytes(b"\x89PNG\r\n")  # not even a real image; engine is "absent" anyway
    res = ocr_tts.ocr_image(img)
    assert res.status == "quarantined"
    assert res.text is None  # THE cardinal check: no invented content
    assert res.reason == "forced_absent"
    assert res.engine is None


def test_ingest_image_without_engine_writes_no_corpus_doc(monkeypatch, tmp_path):
    """Anti-evidence-theater: a missing engine must not materialize ANY corpus
    doc; it must quarantine instead."""
    monkeypatch.setattr(ocr_tts, "ocr_engine_status", lambda: (False, "forced_absent"))
    ws = _make_workspace(tmp_path)
    img = tmp_path / "scan.png"
    img.write_bytes(b"not-an-image")
    result = ocr_tts.ingest_image(img, workspace=ws, reindex=False)
    assert result["status"] == "quarantined"
    assert result.get("corpus_doc") is None
    assert _shard_docs(ws) == []  # nothing entered the corpus
    q = ws / "library" / "cortex-library" / ocr_tts.QUARANTINE_REL
    assert q.exists()
    recs = [json.loads(line) for line in q.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert recs and recs[-1]["reason"] == "forced_absent"


# --------------------------------------------------------------------------- #
# (c) OCR'd text flows into the ingest/corpus path (engine-independent)
# --------------------------------------------------------------------------- #
def test_materialize_ocr_doc_is_discoverable_by_the_existing_index(tmp_path):
    from cortex_core.search import CortexSearchIndex

    ws = _make_workspace(tmp_path)
    text = "Photosynthesis converts sunlight into chemical energy in chloroplasts."
    out = ocr_tts.materialize_ocr_doc(
        text, source="scan-001.png", workspace=ws, engine="tesseract-test", reindex=True
    )
    doc_path = Path(out["corpus_doc"])
    assert doc_path.exists()
    assert doc_path.parent.name == ocr_tts.OCR_SHARD
    body = doc_path.read_text(encoding="utf-8")
    assert "ocr_engine:" in body and text in body  # provenance + real text, no theater

    hits = CortexSearchIndex(ws).search("chloroplasts", limit=5)
    assert any("chloroplasts" in (h.snippet or "").lower() or "scan-001" in h.filename for h in hits)


def test_materialize_is_idempotent(tmp_path):
    ws = _make_workspace(tmp_path)
    text = "Deterministic corpus content for dedupe."
    a = ocr_tts.materialize_ocr_doc(text, source="a.png", workspace=ws, engine="e", reindex=False)
    b = ocr_tts.materialize_ocr_doc(text, source="a.png", workspace=ws, engine="e", reindex=False)
    assert a["corpus_doc"] == b["corpus_doc"]
    assert len(_shard_docs(ws)) == 1  # same source+content -> one doc, not two


# --------------------------------------------------------------------------- #
# (a) REAL OCR when the engine is actually present on this machine
# --------------------------------------------------------------------------- #
def test_real_ocr_recognizes_known_text_when_engine_present(tmp_path):
    available, reason = ocr_tts.ocr_engine_status()
    if not available:
        pytest.skip(f"OCR engine unavailable on this machine: {reason}")
    img = tmp_path / "known.png"
    if not _make_text_image(img, "CORTEX OCR"):
        pytest.skip("PIL unavailable to render a test image")
    res = ocr_tts.ocr_image(img)
    assert res.status == "ok"
    assert res.text is not None
    norm = "".join(c for c in res.text.upper() if c.isalnum())
    assert "CORTEX" in norm  # real recognition, not a fixed string


def test_real_ocr_end_to_end_flows_into_corpus_when_engine_present(tmp_path):
    available, reason = ocr_tts.ocr_engine_status()
    if not available:
        pytest.skip(f"OCR engine unavailable on this machine: {reason}")
    img = tmp_path / "known.png"
    if not _make_text_image(img, "PHOTOSYNTHESIS"):
        pytest.skip("PIL unavailable")
    ws = _make_workspace(tmp_path)
    result = ocr_tts.ingest_image(img, workspace=ws, reindex=True)
    assert result["status"] == "ok"
    assert _shard_docs(ws)  # a doc really landed


# --------------------------------------------------------------------------- #
# TTS: real when available, labeled stub when not
# --------------------------------------------------------------------------- #
def test_real_tts_produces_valid_wav_when_engine_present(tmp_path):
    available, reason = ocr_tts.tts_engine_status()
    if not available:
        pytest.skip(f"TTS engine unavailable on this machine: {reason}")
    out = tmp_path / "speech.wav"
    res = ocr_tts.synthesize_speech("hello cortex", out)
    assert res.status == "ok"
    assert Path(res.wav_path).exists()
    with wave.open(str(res.wav_path)) as w:
        assert w.getnframes() > 0  # real audio, not an empty header
    assert res.duration_s and res.duration_s > 0


def test_tts_quarantines_when_engine_absent(monkeypatch, tmp_path):
    monkeypatch.setattr(ocr_tts, "tts_engine_status", lambda: (False, "forced_absent"))
    out = tmp_path / "speech.wav"
    res = ocr_tts.synthesize_speech("hello", out)
    assert res.status == "quarantined"
    assert res.wav_path is None
    assert not out.exists()  # no empty/fake wav written


# --------------------------------------------------------------------------- #
# Timing map is honestly labeled as an estimate (not fake forced alignment)
# --------------------------------------------------------------------------- #
def test_timing_map_is_labeled_estimate_and_monotonic(tmp_path):
    tm = ocr_tts.timing_map("one two three", duration_s=3.0)
    assert tm["method"] == "proportional_estimate"  # honest label, not "forced_alignment"
    words = tm["words"]
    assert [w["word"] for w in words] == ["one", "two", "three"]
    assert words[0]["start"] == 0.0
    assert abs(words[-1]["end"] - 3.0) < 1e-6
    for a, b in zip(words, words[1:], strict=False):
        assert a["end"] <= b["start"] + 1e-9  # non-overlapping, monotonic


# =========================================================================== #
# Pluggable OCR backend upgrade (VLM -> PaddleOCR -> Tesseract)
# =========================================================================== #

# --------------------------------------------------------------------------- #
# (a) Backend selection prefers VLM -> PaddleOCR -> Tesseract by availability
# --------------------------------------------------------------------------- #
def _force_probes(monkeypatch, *, vlm, paddle, tess):
    """Monkeypatch each backend probe to a fixed (available, label) tuple."""
    monkeypatch.setattr(ocr_tts, "_vlm_probe", lambda env=None: vlm)
    monkeypatch.setattr(ocr_tts, "_paddle_probe", lambda env=None: paddle)
    monkeypatch.setattr(ocr_tts, "_tesseract_probe", lambda env=None: tess)


def test_selection_prefers_vlm_when_all_available(monkeypatch):
    _force_probes(
        monkeypatch,
        vlm=(True, "vlm:qwen-vl"),
        paddle=(True, "paddleocr"),
        tess=(True, "tesseract 5.3"),
    )
    name, label, _ = ocr_tts.select_ocr_backend()
    assert name == "vlm" and label == "vlm:qwen-vl"  # strongest wins


def test_selection_falls_back_to_paddle_when_vlm_absent(monkeypatch):
    _force_probes(
        monkeypatch,
        vlm=(False, "vlm_ocr_not_configured"),
        paddle=(True, "paddleocr"),
        tess=(True, "tesseract 5.3"),
    )
    name, label, reasons = ocr_tts.select_ocr_backend()
    assert name == "paddleocr" and label == "paddleocr"
    assert reasons["vlm"] == "vlm_ocr_not_configured"  # skip reason recorded


def test_selection_falls_back_to_tesseract_last(monkeypatch):
    _force_probes(
        monkeypatch,
        vlm=(False, "vlm_ocr_not_configured"),
        paddle=(False, "paddleocr_not_installed"),
        tess=(True, "tesseract 5.3"),
    )
    name, label, reasons = ocr_tts.select_ocr_backend()
    assert name == "tesseract"  # last-resort floor
    assert reasons["vlm"] == "vlm_ocr_not_configured"
    assert reasons["paddleocr"] == "paddleocr_not_installed"


def test_selection_none_available_aggregates_reasons(monkeypatch):
    _force_probes(
        monkeypatch,
        vlm=(False, "vlm_ocr_not_configured"),
        paddle=(False, "paddleocr_not_installed"),
        tess=(False, "tesseract_binary_missing"),
    )
    name, label, reasons = ocr_tts.select_ocr_backend()
    assert name is None and label is None
    available, reason = ocr_tts.ocr_engine_status()
    assert available is False
    assert "tesseract_binary_missing" in reason and "vlm_ocr_not_configured" in reason


def test_prefer_forces_single_backend_no_fallback(monkeypatch):
    # VLM available, but caller forces tesseract -> tesseract is used, not VLM.
    _force_probes(
        monkeypatch,
        vlm=(True, "vlm:qwen-vl"),
        paddle=(True, "paddleocr"),
        tess=(True, "tesseract 5.3"),
    )
    name, _label, _ = ocr_tts.select_ocr_backend(prefer="tesseract")
    assert name == "tesseract"
    # Forcing an unavailable backend does NOT silently fall back: it reports none.
    _force_probes(
        monkeypatch,
        vlm=(True, "vlm:qwen-vl"),
        paddle=(False, "paddleocr_not_installed"),
        tess=(True, "tesseract 5.3"),
    )
    name2, _l2, reasons2 = ocr_tts.select_ocr_backend(prefer="paddleocr")
    assert name2 is None and reasons2 == {"paddleocr": "paddleocr_not_installed"}


# --------------------------------------------------------------------------- #
# (b) A real image OCRs when a backend is present -- VLM path via injected transport
#     (deterministic, no network; the real-Tesseract path is covered above).
# --------------------------------------------------------------------------- #
class _FakeResp:
    def __init__(self, content):
        self._content = content

    def raise_for_status(self):
        return None

    def json(self):
        return {"choices": [{"message": {"content": self._content}}]}


def _vlm_env():
    return {
        "VLM_OCR_API_URL": "https://example.test/v1",
        "VLM_OCR_API_KEY": "test-key",
        "VLM_OCR_MODEL": "qwen-vl-test",
    }


def test_vlm_backend_ocrs_image_with_injected_transport(tmp_path):
    img = tmp_path / "scan.png"
    img.write_bytes(b"\x89PNG\r\nfake-bytes")  # bytes only get base64'd + sent

    captured = {}

    def fake_post(url, headers=None, json=None):
        captured["url"] = url
        captured["json"] = json
        return _FakeResp("PHOTOSYNTHESIS converts sunlight into energy.")

    res = ocr_tts.ocr_image(img, prefer="vlm", env=_vlm_env(), http_post=fake_post)
    assert res.status == "ok"
    assert res.backend == "vlm"
    assert res.engine == "vlm:qwen-vl-test"
    assert "PHOTOSYNTHESIS" in res.text
    # Provenance / contract of the request: right endpoint, image sent, floor honored.
    assert captured["url"].endswith("/chat/completions")
    assert captured["json"]["max_tokens"] >= ocr_tts.VLM_OCR_MIN_MAX_TOKENS  # 12000 floor
    parts = captured["json"]["messages"][-1]["content"]
    assert any(p.get("type") == "image_url" for p in parts)  # the image really went out


def test_vlm_sentinel_response_quarantines_no_fabrication(tmp_path):
    """A blank page -> the model returns the NO_TEXT sentinel -> honest quarantine,
    never invented words."""
    img = tmp_path / "blank.png"
    img.write_bytes(b"\x89PNG\r\n")

    def fake_post(url, headers=None, json=None):
        return _FakeResp(ocr_tts._VLM_NO_TEXT_SENTINEL)

    res = ocr_tts.ocr_image(img, prefer="vlm", env=_vlm_env(), http_post=fake_post)
    assert res.status == "quarantined"
    assert res.text is None  # cardinal rule: no fabrication
    assert res.reason == "no_text_detected"
    assert res.backend == "vlm"


def test_vlm_transport_failure_quarantines(tmp_path):
    img = tmp_path / "scan.png"
    img.write_bytes(b"\x89PNG\r\n")

    def boom(url, headers=None, json=None):
        raise RuntimeError("network down")

    res = ocr_tts.ocr_image(img, prefer="vlm", env=_vlm_env(), http_post=boom)
    assert res.status == "quarantined"
    assert res.text is None
    assert res.backend == "vlm"
    assert res.reason.startswith("vlm_call_failed")


# --------------------------------------------------------------------------- #
# (d) The chosen engine/backend is recorded (provenance) end-to-end
# --------------------------------------------------------------------------- #
def test_ingest_records_backend_and_engine_provenance(tmp_path):
    ws = _make_workspace(tmp_path)
    img = tmp_path / "scan.png"
    img.write_bytes(b"\x89PNG\r\n")

    def fake_post(url, headers=None, json=None):
        return _FakeResp("Chloroplasts perform photosynthesis.")

    result = ocr_tts.ingest_image(
        img, workspace=ws, prefer="vlm", env=_vlm_env(), http_post=fake_post, reindex=False
    )
    assert result["status"] == "ok"
    assert result["backend"] == "vlm"
    assert result["engine"] == "vlm:qwen-vl-test"
    # Provenance is persisted in the doc frontmatter, not just the return value.
    docs = _shard_docs(ws)
    assert len(docs) == 1
    body = docs[0].read_text(encoding="utf-8")
    assert '"vlm"' in body  # ocr_backend: "vlm"
    assert "vlm:qwen-vl-test" in body  # ocr_engine label


def test_quarantine_record_names_the_backend(tmp_path):
    """Even a failed page records WHICH backend was attempted (provenance)."""
    ws = _make_workspace(tmp_path)
    img = tmp_path / "scan.png"
    img.write_bytes(b"\x89PNG\r\n")

    def boom(url, headers=None, json=None):
        raise RuntimeError("nope")

    result = ocr_tts.ingest_image(
        img, workspace=ws, prefer="vlm", env=_vlm_env(), http_post=boom, reindex=False
    )
    assert result["status"] == "quarantined"
    assert result["backend"] == "vlm"
    assert result["corpus_doc"] is None
    assert _shard_docs(ws) == []  # nothing fabricated into the corpus
    q = ws / "library" / "cortex-library" / ocr_tts.QUARANTINE_REL
    rec = [json.loads(line) for line in q.read_text(encoding="utf-8").splitlines() if line.strip()][-1]
    assert rec["backend"] == "vlm"


def test_backends_status_reports_all_three(monkeypatch):
    _force_probes(
        monkeypatch,
        vlm=(False, "vlm_ocr_not_configured"),
        paddle=(False, "paddleocr_not_installed"),
        tess=(True, "tesseract 5.3"),
    )
    status = ocr_tts.ocr_backends_status()
    assert set(status) == {"vlm", "paddleocr", "tesseract"}
    assert status["tesseract"]["available"] is True
    assert status["vlm"]["available"] is False
