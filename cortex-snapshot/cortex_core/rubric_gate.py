"""Rubric-driven verification gate -- the missing REVIEW gate (2026-07-07).

Three independent findings this session converged on one hole: the engine's
REVIEW->CLOSEOUT gate (`state_engine.default_gate`) only checks "is the payload a
dict" -- no *real* verification ever happens before a task can close out; an agent's
own extensive self-verification (build succeeds, jsdom, edge cases) never caught real
CSS overflow / text-clipping / empty-detail layout bugs because none of it ever took a
screenshot and *looked*; and the 12 `calibration/rubrics/*` files -- the accumulated
judging intelligence -- were referenced NOWHERE in `cortex_core/`, pure static material
for calibration experiments, disconnected from the build/review pipeline.

This module connects them. It turns a rubric into a real engine gate, parallel to
`bakeoff.make_coding_gate` (a deterministic checker -> gate), but for the class of defect
a deterministic checker *cannot* see: does the rendered UI actually look right?

Design (matches this repo's injectable-seam convention -- cf. `judge.llm_judge`'s
`http_post`, `bakeoff.make_model_subject`'s `complete`):

    verify_ui_artifact(artifact, rubric,
                       render_fn=...,        # artifact -> [Screenshot]   (Playwright default)
                       vision_judge_fn=...)  # (rubric, shots) -> verdict (Claude-vision default)

Two layers, ordered (the rubric's own `ordering_invariant`):

  Layer 1 -- DETERMINISTIC static scan of the emitted CSS/HTML text. No renderer, no
    model, no cost, no network. Catches the anti-reward-hacking floor a generator cannot
    argue with (Inter-only type, purple-band hero gradient, glassmorphism pile-ups,
    emoji bullets, rounded-2xl+shadow-lg on everything). A Layer-1 BLOCK is final and is
    reached with ZERO judge calls -- the ordering invariant.

  Layer 2 -- the VISION judge, on RENDERED screenshots (initial load + the states reached
    by driving the task's interactions -- because the real empty-detail bug looked fine on
    load and broke only after a click). Only runs on artifacts that cleared Layer 1.

Graceful degradation is a first-class requirement, not an afterthought: if no
vision-capable judge tier is configured (or Playwright isn't installed / a browser won't
launch), the gate DOES NOT crash the pipeline -- Layer 1 alone decides and the judge is
recorded as skipped (the rubric's `judge_standing: ADVISORY` until kappa-calibrated).

`make_verification_gate(...)` composes with `StateEngine(gate=...)` exactly like
`make_coding_gate`: at REVIEW it returns `{"pass": bool, "reason": str}` with a CONCRETE
reason, so a fail flows through the engine's existing `rework_to: IMPLEMENT` loop carrying
the bug description -- not just a boolean -- and the task cannot reach CLOSEOUT until the
UI actually passes.
"""

from __future__ import annotations

import base64
import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import yaml

from .config import resolve_workspace
from .state_engine import GateFn, default_gate, review_scope_gate

# ---------------------------------------------------------------------------
# Data shapes
# ---------------------------------------------------------------------------


@dataclass
class Screenshot:
    """One rendered capture. `label` names the state ("initial", "detail-open");
    `png` is the raw image bytes. The vision judge sees the ordered list."""

    label: str
    png: bytes


@dataclass
class CheckResult:
    """One Layer-1 deterministic check (same shape as evals.rubrics.rubric_c_layer1)."""

    name: str
    status: str  # pass | warn | fail   (fail == BLOCK)
    detail: str


@dataclass
class VisionVerdict:
    """The vision judge's read of the rendered screenshots."""

    met: bool
    reason: str
    criteria: dict[str, Any] = field(default_factory=dict)


@dataclass
class RubricResult:
    """The full verdict verify_ui_artifact returns -- what the gate turns into pass/reason."""

    rubric_id: str
    passed: bool
    reason: str
    layer0: list[CheckResult] = field(default_factory=list)
    layer0_overall: str = "pass"
    layer1: list[CheckResult] = field(default_factory=list)
    layer1_overall: str = "pass"
    judge: VisionVerdict | None = None
    judge_skipped: bool = False
    screenshots: list[str] = field(default_factory=list)  # labels only (bytes not retained)

    def asdict(self) -> dict[str, Any]:
        return {
            "rubric_id": self.rubric_id,
            "passed": self.passed,
            "reason": self.reason,
            "layer0_overall": self.layer0_overall,
            "layer0": [{"name": c.name, "status": c.status, "detail": c.detail} for c in self.layer0],
            "layer1_overall": self.layer1_overall,
            "layer1": [{"name": c.name, "status": c.status, "detail": c.detail} for c in self.layer1],
            "judge": None if self.judge is None else {"met": self.judge.met, "reason": self.judge.reason},
            "judge_skipped": self.judge_skipped,
            "screenshots": self.screenshots,
        }


class RendererUnavailable(RuntimeError):
    """Raised by a render_fn when it cannot render (no Playwright, no browser). Caught by
    verify_ui_artifact and turned into graceful degradation, never a pipeline crash."""


class JudgeUnavailable(RuntimeError):
    """Raised by a vision_judge_fn when no vision-capable judge is configured. Caught by
    verify_ui_artifact and turned into graceful degradation."""


# ---------------------------------------------------------------------------
# Rubric loader
# ---------------------------------------------------------------------------

RUBRICS_DIRNAME = "calibration/rubrics"


def load_rubric(rubric_id: str, workspace: str | Path | None = None) -> dict[str, Any]:
    """Load a rubric YAML from calibration/rubrics/. Accepts "ui_ux", "ui_ux.v1", or a
    filename; resolves the newest matching *.yaml. The rubric is a CANONICAL-plane asset
    (docs/CORTEX-ROUTES-AND-OWNERSHIP.md): the instrument comes from this repo, always."""
    ws = resolve_workspace(workspace)
    d = ws / RUBRICS_DIRNAME
    stem = rubric_id[:-5] if rubric_id.endswith(".yaml") else rubric_id
    candidates = [d / f"{stem}.yaml"]
    if "." not in stem:  # "ui_ux" -> newest ui_ux.vN.yaml
        candidates += sorted(d.glob(f"{stem}.v*.yaml"), reverse=True)
    for c in candidates:
        if c.is_file():
            return yaml.safe_load(c.read_text(encoding="utf-8")) or {}
    raise FileNotFoundError(f"rubric {rubric_id!r} not found under {d}")


# ---------------------------------------------------------------------------
# Artifact reading
# ---------------------------------------------------------------------------


def _read_artifact_text(artifact: str | Path) -> tuple[str, str]:
    """Return (html_text, css_text) for an artifact that is either a single .html file
    or a directory. CSS is every *.css found plus inline <style> blocks -- the Layer-1
    scan works on emitted text alone, no renderer needed."""
    p = Path(artifact)
    html_parts: list[str] = []
    css_parts: list[str] = []
    files: list[Path] = []
    if p.is_dir():
        files = sorted(p.rglob("*.html")) + sorted(p.rglob("*.css"))
    elif p.is_file():
        files = [p] + sorted(p.parent.glob("*.css"))
    for f in files:
        try:
            text = f.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if f.suffix.lower() in (".html", ".htm"):
            html_parts.append(text)
            css_parts.extend(re.findall(r"<style[^>]*>(.*?)</style>", text, re.DOTALL | re.IGNORECASE))
        elif f.suffix.lower() == ".css":
            css_parts.append(text)
    return "\n".join(html_parts), "\n".join(css_parts)


def _entry_html(artifact: str | Path) -> Path | None:
    """The HTML file a renderer should open. index.html wins in a directory."""
    p = Path(artifact)
    if p.is_file() and p.suffix.lower() in (".html", ".htm"):
        return p
    if p.is_dir():
        idx = p / "index.html"
        if idx.is_file():
            return idx
        hits = sorted(p.rglob("*.html"))
        return hits[0] if hits else None
    return None


# ---------------------------------------------------------------------------
# Layer 1 -- deterministic static tells (ui_ux.v1 "available" gates). No renderer.
# ---------------------------------------------------------------------------

# AI-default type stack (the tell is unexamined universality, not the font itself).
_DEFAULT_FONTS = ("inter", "roboto", "arial", "system-ui", "-apple-system", "segoe ui")
# Emoji / dingbat codepoint ranges used as list markers or leading chars.
_EMOJI = re.compile(
    "[\U0001F300-\U0001FAFF\U00002600-\U000027BF\U0001F000-\U0001F0FF\U00002190-\U000021FF\U00002B00-\U00002BFF]"
)


def _font_families(css: str) -> list[str]:
    fams: list[str] = []
    for m in re.finditer(r"font-family\s*:\s*([^;}\n]+)", css, re.IGNORECASE):
        fams.append(m.group(1).strip().lower())
    return fams


def _has_purple_gradient(css: str) -> bool:
    """A hero gradient whose stops both sit in the purple/violet->blue hue band -- the
    single most-cited P0 slop tell. Detected on emitted hex/rgb stops in a linear-gradient."""
    for grad in re.finditer(r"linear-gradient\(([^)]*)\)", css, re.IGNORECASE):
        body = grad.group(1)
        hexes = re.findall(r"#([0-9a-fA-F]{6})", body)
        band = 0
        for h in hexes:
            r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
            # violet->blue->indigo band: blue is the dominant channel, strongly so, and it
            # isn't teal/green (g well below b). Catches both the violet (#7c3aed) and the
            # blue (#3b82f6) end of the canonical purple->blue hero gradient.
            if b == max(r, g, b) and b > 150 and g <= b * 0.75:
                band += 1
        if band >= 2:
            return True
    return False


def layer1_ui_ux(html: str, css: str) -> list[CheckResult]:
    """The deterministic floor for ui_ux.v1: static scans over emitted CSS/HTML text that
    need no renderer and cannot be judge-gamed. `fail` == BLOCK (P0 tells); `warn` ==
    accumulate/route-to-judge (P1/P2). Directional, honest: catches gross tells, not taste."""
    checks: list[CheckResult] = []

    # font_allowlist (P0): the ONLY declared families are the AI-default stack.
    fams = _font_families(css)
    if fams:
        non_generic = [f for f in fams if f not in ("sans-serif", "serif", "monospace", "inherit")]
        all_default = bool(non_generic) and all(
            all(tok.strip().strip("'\"") in _DEFAULT_FONTS or tok.strip().strip("'\"") in
                ("sans-serif", "serif", "monospace") for tok in f.split(","))
            for f in non_generic
        )
        checks.append(CheckResult(
            "font_allowlist", "fail" if all_default else "pass",
            "sole type is the Inter/Roboto/Arial default stack" if all_default else "custom/mixed type present"))

    # purple-band hero gradient (P0).
    pg = _has_purple_gradient(css)
    checks.append(CheckResult("gradient_purple_band", "fail" if pg else "pass",
                              "purple->blue hero gradient" if pg else "none"))

    # permanent dark, no scheme handling (WARN -- dark is legit for dev tools).
    dark_bg = bool(re.search(r"background(-color)?\s*:\s*#0[0-9a-f]{2}[0-9a-f]{3}", css, re.IGNORECASE)) \
        or bool(re.search(r"background(-color)?\s*:\s*#(0\d|1[0-5])", css, re.IGNORECASE))
    scheme = "prefers-color-scheme" in css or "data-theme" in html or "theme-toggle" in html.lower()
    if dark_bg and not scheme:
        checks.append(CheckResult("permanent_dark_no_scheme", "warn", "dark theme, no prefers-color-scheme / toggle"))

    # reflexive glassmorphism (WARN escalating): backdrop-filter blur on many surfaces.
    blur_n = len(re.findall(r"backdrop-filter\s*:\s*[^;}]*blur", css, re.IGNORECASE))
    if blur_n >= 3:
        checks.append(CheckResult("glassmorphism", "warn", f"backdrop blur on {blur_n} surfaces"))

    # rounded-2xl + shadow co-occurrence on many siblings (WARN -- P1 accumulation).
    big_radius = len(re.findall(r"border-radius\s*:\s*(2[4-9]|[3-9]\d)px", css, re.IGNORECASE))
    shadow_n = len(re.findall(r"box-shadow\s*:", css, re.IGNORECASE))
    if big_radius >= 4 and shadow_n >= 4:
        checks.append(CheckResult("rounded2xl_shadow", "warn",
                                  f"{big_radius} large-radius + {shadow_n} shadowed components"))

    # emoji bullets (P1): emoji as leading list-item chars.
    emoji_li = len(_EMOJI.findall(" ".join(re.findall(r"<li[^>]*>\s*(.)", html))))
    if emoji_li >= 2:
        checks.append(CheckResult("emoji_bullets", "warn", f"{emoji_li} emoji list markers"))

    if not checks:
        checks.append(CheckResult("static_scan", "pass", "no deterministic tells found"))
    return checks


def _overall(checks: list[CheckResult]) -> str:
    statuses = {c.status for c in checks}
    return "fail" if "fail" in statuses else ("warn" if "warn" in statuses else "pass")


# ---------------------------------------------------------------------------
# Layer 0 -- deterministic STRUCTURAL floor (2026-07-07 ledger-mining pass). No
# renderer, no model, no network, no credentials. Runs FIRST, blocks hardest, and
# -- crucially -- still bites when the vision judge degrades (no API key / no
# browser), which is exactly when the ui_ux Layer-1 slop scan would otherwise wave a
# broken page through. This closes the single highest-frequency failure enabler in
# the 2026-07-07 real-build benchmark: "verification is theater; REVIEW->CLOSEOUT
# advanced without anyone ever LOADING the deliverable." Three concrete defect
# CLASSES it catches deterministically (each seen multiple times in the benchmark):
#   1. TRUNCATED markup      -- unclosed <style>/<script>/<body>/<html> (task04, task05,
#      task08: the max_tokens single-shot-truncation class the vision judge only
#      catches if it is configured AND renders).
#   2. BROKEN local refs     -- <script src>/<link href> naming a file that does not
#      exist in the run dir (task03: index.html loaded data.js/app.js, neither existed).
#   3. ELISION placeholders  -- write_file content that is itself "..." / "<!DOCTYPE
#      html>..." / "[rest of file]" (task13 = 3-byte "...", task15 = 18-byte
#      "<!DOCTYPE html>...": a well-formed tool call whose CONTENT is a lazy stub).
# ---------------------------------------------------------------------------

# Whole-file content that is nothing but an elision placeholder.
_ELISION_WHOLE = {"...", "…", "<!-- ... -->", "/* ... */"}
# Lazy "I'll fill this in later" markers a generator emits instead of real content.
_ELISION_MARKERS = re.compile(
    r"(\[\s*(?:rest|remainder|\.\.\.|omitted|truncated)[^\]]*\]"
    r"|(?:rest|remainder) of (?:the )?(?:file|code|html|content|markup)"
    r"|<!--\s*\.\.\.\s*(?:rest|remainder|etc)?[^>]*-->"
    r"|/\*\s*\.\.\.\s*(?:rest|remainder|etc)[^*]*\*/)",
    re.IGNORECASE,
)


def _placeholder_reason(text: str) -> str | None:
    """Return why `text` is an elision/placeholder stub, or None if it is real content.
    Conservative on purpose: a legitimate page contains "..." in prose, so a bare
    ellipsis only trips when it IS essentially the whole (tiny) file, not when it
    appears inside real markup."""
    stripped = text.strip()
    if not stripped:
        return "file is empty"
    if stripped in _ELISION_WHOLE or set(stripped) <= {".", "…", " ", "\n"}:
        return f"file content is an elision placeholder ({stripped[:40]!r})"
    # A tiny file ending in an ellipsis is the "<!DOCTYPE html>..." lazy-stub class.
    if len(stripped) < 80 and stripped.rstrip().endswith(("...", "…")):
        return f"file is a truncated stub ending in an ellipsis ({stripped[:60]!r})"
    m = _ELISION_MARKERS.search(stripped)
    if m:
        return f"file contains a lazy elision marker ({m.group(0)[:60]!r})"
    return None


def _unbalanced_markup(html: str) -> list[str]:
    """Opening vs closing counts for the structural container tags. An opened-but-
    never-closed <style>/<script>/<body>/<html> is the signature of a payload cut off
    mid-generation (undersized max_tokens). Only tags that WERE opened are checked, so a
    legitimate HTML fragment that never opens <html>/<body> is not false-flagged."""
    problems: list[str] = []
    for tag in ("html", "head", "body", "style", "script"):
        opens = len(re.findall(rf"<{tag}(?:\s[^>]*)?>", html, re.IGNORECASE))
        closes = len(re.findall(rf"</{tag}\s*>", html, re.IGNORECASE))
        if opens > closes:
            problems.append(f"<{tag}> opened {opens}x but closed {closes}x")
    return problems


def _broken_local_refs(html_files: list[Path]) -> list[str]:
    """Every <script src> / <link href> naming a LOCAL file that must resolve in the
    same directory tree. External (http/https/protocol-relative/data/anchor) refs are
    skipped -- only local sibling files are required to exist on disk."""
    missing: list[str] = []
    ref_re = re.compile(
        r"""<(?:script[^>]*?\ssrc|link[^>]*?\shref)\s*=\s*['"]([^'"]+)['"]""", re.IGNORECASE)
    for hf in html_files:
        try:
            text = hf.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        base = hf.parent
        for m in ref_re.finditer(text):
            ref = m.group(1).strip()
            if not ref or ref.startswith(("http://", "https://", "//", "data:", "#", "mailto:", "javascript:")):
                continue
            local = ref.split("?", 1)[0].split("#", 1)[0]
            if not local:
                continue
            if not (base / local).is_file():
                missing.append(f"{hf.name} references {ref!r} which does not exist on disk")
    return missing


def _artifact_files(artifact: str | Path) -> tuple[list[Path], list[Path]]:
    """(html_files, all_text_files) for an artifact that is a single .html file or a
    directory of a built deliverable. all_text_files is html/css/js -- the set a
    placeholder-stub scan applies to."""
    p = Path(artifact)
    html: list[Path] = []
    text: list[Path] = []
    if p.is_dir():
        html = sorted(p.rglob("*.html")) + sorted(p.rglob("*.htm"))
        text = html + sorted(p.rglob("*.css")) + sorted(p.rglob("*.js"))
    elif p.is_file():
        if p.suffix.lower() in (".html", ".htm"):
            html = [p]
        text = [p] + sorted(p.parent.glob("*.css")) + sorted(p.parent.glob("*.js"))
    return html, text


def layer0_structural(artifact: str | Path) -> list[CheckResult]:
    """Deterministic structural verification of a built web artifact -- no renderer,
    no model. Returns `fail` CheckResults for truncated markup, broken local script/
    style references, and elision-placeholder file content; a single `pass` when the
    deliverable is structurally whole. This is the anti-verification-theater floor: it
    decides pass/fail from the FILES THEMSELVES, never a self-report."""
    html_files, text_files = _artifact_files(artifact)
    checks: list[CheckResult] = []

    for f in text_files:
        try:
            content = f.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        reason = _placeholder_reason(content)
        if reason:
            checks.append(CheckResult("elision_placeholder", "fail", f"{f.name}: {reason}"))

    for hf in html_files:
        try:
            content = hf.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for prob in _unbalanced_markup(content):
            checks.append(CheckResult(
                "unclosed_markup", "fail",
                f"{hf.name}: {prob} -- markup is truncated/incomplete, the page will not render"))

    for miss in _broken_local_refs(html_files):
        checks.append(CheckResult("broken_reference", "fail", miss))

    if not checks:
        checks.append(CheckResult(
            "structural", "pass", "markup complete, local refs resolve, no placeholder stubs"))
    return checks


# Registry: rubric_id -> Layer-1 function. Only ui_ux has a wired deterministic layer today.
_LAYER1: dict[str, Callable[[str, str], list[CheckResult]]] = {
    "ui_ux": layer1_ui_ux,
    "ui_ux.v1": layer1_ui_ux,
}


# ---------------------------------------------------------------------------
# Default Playwright renderer (initial load + interaction states)
# ---------------------------------------------------------------------------


def playwright_render(
    artifact: str | Path,
    interactions: list[dict[str, Any]] | None = None,
    *,
    viewport: tuple[int, int] = (1440, 900),
    timeout_ms: int = 8000,
) -> list[Screenshot]:
    """Render `artifact` with Playwright, capturing the initial load AND the states the
    task's interactions reach -- the empty-detail bug looked fine on load and only broke
    after a click, so a static initial screenshot would have missed it (today's finding).

    `interactions` is a list of steps, each {"action": "click"|"wait", "selector"?:...,
    "ms"?:..., "label"?:...}; a labeled screenshot is captured after each. For a static
    report page pass interactions=None -> a single initial screenshot (use judgment, don't
    force interaction-testing on something with no flow).

    Raises RendererUnavailable if Playwright isn't installed or a browser won't launch --
    the caller degrades gracefully rather than crashing the pipeline.
    """
    entry = _entry_html(artifact)
    if entry is None:
        raise RendererUnavailable(f"no HTML entry file found in {artifact}")
    try:
        from playwright.sync_api import sync_playwright
    except Exception as exc:  # noqa: BLE001
        raise RendererUnavailable(f"playwright not importable: {exc!r}") from exc

    shots: list[Screenshot] = []
    url = entry.resolve().as_uri()
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch()
            try:
                page = browser.new_page(viewport={"width": viewport[0], "height": viewport[1]})
                page.goto(url, timeout=timeout_ms, wait_until="load")
                shots.append(Screenshot("initial", page.screenshot()))
                for i, step in enumerate(interactions or []):
                    action = step.get("action", "click")
                    label = step.get("label", f"{action}-{i + 1}")
                    try:
                        if action == "click":
                            page.click(step["selector"], timeout=timeout_ms)
                        elif action == "wait":
                            page.wait_for_timeout(int(step.get("ms", 300)))
                        page.wait_for_timeout(150)  # settle
                        shots.append(Screenshot(label, page.screenshot()))
                    except Exception:  # noqa: BLE001 -- a bad selector is a captured signal, not a crash
                        shots.append(Screenshot(f"{label}-FAILED", page.screenshot()))
            finally:
                browser.close()
    except RendererUnavailable:
        raise
    except Exception as exc:  # noqa: BLE001 -- launch/nav failure -> degrade, don't crash
        raise RendererUnavailable(f"playwright render failed: {exc!r}") from exc
    return shots


# ---------------------------------------------------------------------------
# Default Claude-vision judge (anthropic SDK, base64 PNG)
# ---------------------------------------------------------------------------

# Cheap vision-capable default per ui_ux.v1's judge_tier_note ("scaling the judge barely
# moves agreement on aesthetics -- invest in anchoring, not size"). Override via env.
_DEFAULT_VISION_MODEL = os.environ.get("RUBRIC_JUDGE_MODEL", "claude-haiku-4-5")

_VISION_SYSTEM = """You are an impartial UI-quality EVALUATOR. You are shown one or more \
RENDERED SCREENSHOTS of a built interface (initial load and states reached by interacting \
with it) plus the rubric criteria. Grade the ARTIFACT, not any narrative about it (you are \
given no such narrative -- MARCH asymmetry).

Look for real rendering defects a build/test pass cannot see: text clipped or overflowing \
its container, elements colliding or overlapping, an empty/collapsed panel where content \
should be, broken layout after an interaction, unreadable contrast, content cut off. These \
concrete failures matter more than taste.

Return ONLY a JSON object: {"met": true|false, "reason": "<one or two concrete sentences \
naming the specific defect(s) or confirming the states render correctly>", \
"criteria": {"<vj-id>": "MET"|"UNMET"|"NA"}}. \
"met" is false if ANY screenshot shows a concrete rendering/layout defect."""


def claude_vision_judge(
    rubric: dict[str, Any],
    screenshots: list[Screenshot],
    *,
    context: str = "",
    model: str | None = None,
    max_tokens: int = 700,
    client: Any = None,
) -> VisionVerdict:
    """Grade rendered screenshots against the rubric with a Claude vision model.

    Uses the anthropic SDK (already a repo dependency). `client` is an injection seam for
    tests (any object with `.messages.create(...)`), defaulting to `anthropic.Anthropic()`.
    Raises JudgeUnavailable if the SDK/credentials aren't available -- the caller degrades.
    """
    if not screenshots:
        raise JudgeUnavailable("no screenshots to judge")
    if client is None:
        try:
            import anthropic

            client = anthropic.Anthropic()
        except Exception as exc:  # noqa: BLE001 -- no SDK or no credentials
            raise JudgeUnavailable(f"anthropic client unavailable: {exc!r}") from exc

    # Build the Layer-2 criteria list from the rubric for the prompt.
    vj = rubric.get("layer_2_vlm_judge", []) if isinstance(rubric, dict) else []
    crit_lines = [f"- {c.get('id')}: {(c.get('question') or '').strip()[:200]}" for c in vj]
    text = ("Rubric criteria (Layer 2):\n" + "\n".join(crit_lines) + "\n\n" if crit_lines else "")
    if context:
        text += f"Task/brief context: {context}\n\n"
    text += "Screenshots follow in order: " + ", ".join(s.label for s in screenshots) + "."

    content: list[dict[str, Any]] = [{"type": "text", "text": text}]
    for s in screenshots:
        content.append({"type": "text", "text": f"[{s.label}]"})
        content.append({
            "type": "image",
            "source": {"type": "base64", "media_type": "image/png",
                       "data": base64.standard_b64encode(s.png).decode("ascii")},
        })

    try:
        resp = client.messages.create(
            model=model or _DEFAULT_VISION_MODEL,
            max_tokens=max_tokens,
            system=_VISION_SYSTEM,
            messages=[{"role": "user", "content": content}],
        )
    except Exception as exc:  # noqa: BLE001 -- transport/auth -> degrade
        raise JudgeUnavailable(f"vision judge call failed: {exc!r}") from exc

    raw = ""
    for block in getattr(resp, "content", []) or []:
        if getattr(block, "type", None) == "text":
            raw += block.text
    from .llm_parse import extract_json_object

    obj = extract_json_object(raw) or {}
    met = bool(obj.get("met", False))
    reason = str(obj.get("reason", "")).strip() or "(no reason given)"
    return VisionVerdict(met=met, reason=reason, criteria=obj.get("criteria", {}) if isinstance(obj.get("criteria"), dict) else {})


# ---------------------------------------------------------------------------
# OpenRouter vision judge (Qwen2.5-VL) -- fallback when no ANTHROPIC_API_KEY is configured.
# ---------------------------------------------------------------------------

# Confirmed live (2026-07-07): no "8B" Qwen2.5-VL variant exists on OpenRouter; 72B is the real,
# resolvable model ID (verified via a live vision call, not assumed) and is a stronger judge than
# an 8B model would have been anyway.
_OPENROUTER_VISION_MODEL = os.environ.get("RUBRIC_JUDGE_OPENROUTER_MODEL", "qwen/qwen2.5-vl-72b-instruct")


def openrouter_vision_judge(
    rubric: dict[str, Any],
    screenshots: list[Screenshot],
    *,
    context: str = "",
    model: str | None = None,
    max_tokens: int = 700,
) -> VisionVerdict:
    """Grade rendered screenshots against the rubric with a Qwen2.5-VL vision model via
    OpenRouter -- the fallback judge when ANTHROPIC_API_KEY isn't configured (this repo's
    OPENROUTER_API_KEY is a lower-cost, already-configured lane -- see cortex_core/judge.py).
    Same OpenAI-compatible image_url/data-URI content shape `_llm_complete` uses for text-only
    calls, extended here with real image content. Raises JudgeUnavailable on any failure so the
    caller degrades the same way it does for the Claude judge -- never a silent fabricated pass."""
    if not screenshots:
        raise JudgeUnavailable("no screenshots to judge")
    try:
        from . import judge as _J
        import httpx

        cfg = _J.get_tier_config("openrouter", env=_J.load_env())
    except Exception as exc:  # noqa: BLE001 -- tier not configured
        raise JudgeUnavailable(f"openrouter tier unavailable: {exc!r}") from exc

    vj = rubric.get("layer_2_vlm_judge", []) if isinstance(rubric, dict) else []
    crit_lines = [f"- {c.get('id')}: {(c.get('question') or '').strip()[:200]}" for c in vj]
    text = ("Rubric criteria (Layer 2):\n" + "\n".join(crit_lines) + "\n\n" if crit_lines else "")
    if context:
        text += f"Task/brief context: {context}\n\n"
    text += "Screenshots follow in order: " + ", ".join(s.label for s in screenshots) + "."

    content: list[dict[str, Any]] = [{"type": "text", "text": _VISION_SYSTEM + "\n\n" + text}]
    for s in screenshots:
        content.append({
            "type": "image_url",
            "image_url": {"url": "data:image/png;base64," + base64.standard_b64encode(s.png).decode("ascii")},
        })

    try:
        resp = httpx.post(
            _J._chat_completions_url(cfg.url),
            headers={"Authorization": f"Bearer {cfg.key}", "Content-Type": "application/json"},
            json={"model": model or _OPENROUTER_VISION_MODEL,
                  "messages": [{"role": "user", "content": content}], "max_tokens": max_tokens},
            timeout=60,
        )
        resp.raise_for_status()
        raw = _J._extract_content(resp.json()) or ""
    except Exception as exc:  # noqa: BLE001 -- transport/auth/rate-limit -> degrade
        raise JudgeUnavailable(f"openrouter vision judge call failed: {exc!r}") from exc

    from .llm_parse import extract_json_object
    obj = extract_json_object(raw) or {}
    met = bool(obj.get("met", False))
    reason = str(obj.get("reason", "")).strip() or "(no reason given)"
    return VisionVerdict(met=met, reason=reason, criteria=obj.get("criteria", {}) if isinstance(obj.get("criteria"), dict) else {})


def default_vision_judge(
    rubric: dict[str, Any],
    screenshots: list[Screenshot],
    *,
    context: str = "",
) -> VisionVerdict:
    """Try Claude vision first (highest-anchored judge in this repo's ladder), then degrade to
    the OpenRouter Qwen2.5-VL judge, so a missing ANTHROPIC_API_KEY no longer means "no real
    judge at all" -- it means a real, cheaper vision-capable fallback instead of an advisory
    human stand-in. Only raises JudgeUnavailable (letting the caller's own graceful-degradation
    path handle it) if BOTH are unavailable."""
    try:
        return claude_vision_judge(rubric, screenshots, context=context)
    except JudgeUnavailable:
        pass
    return openrouter_vision_judge(rubric, screenshots, context=context)


# ---------------------------------------------------------------------------
# The orchestrator: verify one artifact against one rubric
# ---------------------------------------------------------------------------


def verify_ui_artifact(
    artifact: str | Path,
    rubric: dict[str, Any],
    *,
    rubric_id: str = "ui_ux",
    interactions: list[dict[str, Any]] | None = None,
    context: str = "",
    render_fn: Callable[..., list[Screenshot]] | None = None,
    vision_judge_fn: Callable[..., VisionVerdict] | None = None,
) -> RubricResult:
    """Verify a UI artifact: Layer-1 static scan (blocking, no cost) THEN, only if it
    cleared Layer 1, the Layer-2 vision judge on rendered screenshots.

    Ordering invariant (from ui_ux.v1): Layer 0 (structural) runs first and a BLOCK is
    final; then Layer 1 (slop tells) runs and a BLOCK is final -- no screenshot score
    overrides a failed deterministic check, and the judge is never called on a Layer-0/1
    failure. Graceful degradation: if the renderer or judge is unavailable, the judge is
    recorded as skipped and the deterministic layers alone decide -- the pipeline never
    crashes. Layer 0 is the load-bearing anti-truncation/anti-placeholder floor that still
    bites when the vision judge degrades (exactly when a truncated page would otherwise pass).
    """
    # Layer 0 -- deterministic structural floor. Cheapest, hardest, always runs first.
    l0_checks = layer0_structural(artifact)
    l0 = _overall(l0_checks)
    if l0 == "fail":
        blocking0 = [c for c in l0_checks if c.status == "fail"]
        reason = ("Layer-0 STRUCTURAL BLOCK (the built artifact is truncated, references a "
                  "missing file, or is a placeholder stub): "
                  + "; ".join(f"{c.name} ({c.detail})" for c in blocking0))
        return RubricResult(rubric_id=rubric_id, passed=False, reason=reason,
                            layer0=l0_checks, layer0_overall=l0)

    html, css = _read_artifact_text(artifact)
    layer1_fn = _LAYER1.get(rubric_id, _LAYER1.get(rubric_id.split(".")[0]))
    checks = layer1_fn(html, css) if layer1_fn else [CheckResult("static_scan", "pass", "no Layer-1 for this rubric")]
    l1 = _overall(checks)

    if l1 == "fail":
        blocking = [c for c in checks if c.status == "fail"]
        reason = "Layer-1 BLOCK (deterministic slop tells): " + "; ".join(f"{c.name} ({c.detail})" for c in blocking)
        return RubricResult(rubric_id=rubric_id, passed=False, reason=reason,
                            layer0=l0_checks, layer0_overall=l0,
                            layer1=checks, layer1_overall=l1)

    # Layer 2 -- render + judge, degrading gracefully.
    render_fn = render_fn or playwright_render
    vision_judge_fn = vision_judge_fn or default_vision_judge
    try:
        shots = render_fn(artifact, interactions)
    except RendererUnavailable as exc:
        return RubricResult(rubric_id=rubric_id, passed=True, judge_skipped=True,
                            reason=f"Layer-0 {l0}; Layer-1 {l1}; vision layer SKIPPED (renderer unavailable: {exc}) -- "
                                   "advisory judge not run, deterministic gates alone decided.",
                            layer0=l0_checks, layer0_overall=l0,
                            layer1=checks, layer1_overall=l1)
    try:
        verdict = vision_judge_fn(rubric, shots, context=context)
    except JudgeUnavailable as exc:
        return RubricResult(rubric_id=rubric_id, passed=True, judge_skipped=True,
                            reason=f"Layer-0 {l0}; Layer-1 {l1}; vision layer SKIPPED (no vision judge configured: {exc}) -- "
                                   "deterministic gates alone decided.",
                            layer0=l0_checks, layer0_overall=l0,
                            layer1=checks, layer1_overall=l1,
                            screenshots=[s.label for s in shots])

    passed = bool(verdict.met)
    reason = (f"Layer-0 {l0}; Layer-1 {l1}; vision judge: {verdict.reason}" if passed
              else f"Vision judge REFUSED: {verdict.reason}")
    return RubricResult(rubric_id=rubric_id, passed=passed, reason=reason,
                        layer0=l0_checks, layer0_overall=l0, layer1=checks,
                        layer1_overall=l1, judge=verdict, screenshots=[s.label for s in shots])


# ---------------------------------------------------------------------------
# KEDB hook: a repeated rubric-gate failure IS a repeated-failure signal
# ---------------------------------------------------------------------------


def append_gate_failure(record: dict[str, Any], workspace: str | Path | None = None) -> Path:
    """Append a gate failure to patterns/gate_failures.jsonl so a recurring visual-defect
    CLASS accumulates a signal the KEDB detector (patterns.promote_candidates) can surface
    into an authored pattern. A single failure is noise; a repeated one is a pattern."""
    ws = resolve_workspace(workspace)
    d = ws / "patterns"
    d.mkdir(parents=True, exist_ok=True)
    path = d / "gate_failures.jsonl"
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, default=str) + "\n")
    return path


# ---------------------------------------------------------------------------
# The engine gate -- parallel to bakeoff.make_coding_gate
# ---------------------------------------------------------------------------


def _default_artifact_resolver(payload: Any) -> str | None:
    """Pull the built-UI path out of a REVIEW payload. Agents submit it as one of these
    keys; None means "no artifact provided"."""
    if not isinstance(payload, dict):
        return None
    for k in ("artifact", "artifact_path", "artifact_dir", "ui", "build_dir"):
        v = payload.get(k)
        if v:
            return str(v)
    return None


def make_text_rubric_gate(
    check: Callable[[str], "tuple[bool, str]"],
    *,
    phases: tuple[str, ...] = ("REVIEW",),
    payload_keys: tuple[str, ...] = ("text", "handoff", "result", "review"),
    on_fail: Callable[[str, str], None] | None = None,
) -> Callable[[str, dict[str, Any], Any], dict[str, Any]]:
    """The TEXT analogue of make_verification_gate, for rubrics whose signal is emitted
    text rather than rendered pixels (handoff completeness, actionable-item well-formedness).

    `check(text) -> (passed, reason)` is any deterministic text checker (e.g. a wrapper
    around `evals.rubrics.rubric_c_layer1`). Same engine contract as make_verification_gate:
    at REVIEW it returns `{"pass", "reason"}` with a concrete reason so a fail flows through
    the existing rework loop; fail-closed on a raising checker; defers to default_gate
    elsewhere. Reuses the checker->gate mechanism rather than building a new judge path."""
    def gate(phase: str, task: dict[str, Any], payload: Any) -> dict[str, Any]:
        if phase not in phases:
            return default_gate(phase, task, payload)
        text = ""
        if isinstance(payload, dict):
            for k in payload_keys:
                if payload.get(k):
                    text = str(payload[k])
                    break
        try:
            passed, reason = check(text)
        except Exception as exc:  # noqa: BLE001 -- fail-closed
            return {"pass": False, "reason": f"text-rubric gate raised: {exc!r}"}
        if not passed and on_fail is not None:
            try:
                on_fail(phase, reason)
            except Exception:  # noqa: BLE001
                pass
        return {"pass": bool(passed), "reason": str(reason)}

    return gate


def handoff_check(text: str) -> "tuple[bool, str]":
    """Deterministic handoff / actionable-item check built on the EXISTING, tested
    `evals.rubrics.rubric_c_layer1` (field presence + requirement-smell + EARS heuristic).
    Blocks only on a hard `fail` (the rubric's own rule: a judge must not override a
    deterministic fail); `needs_review` passes but its reasons ride along in the message."""
    from evals.rubrics.rubric_c_layer1 import rubric_c_layer1

    r = rubric_c_layer1(text)
    bad = [f"{c.name} ({c.detail})" for c in r.checks if c.status == "fail"]
    if r.overall == "fail":
        return False, "Handoff rubric BLOCK (deterministic): " + "; ".join(bad)
    flagged = [c.name for c in r.checks if c.status == "needs_review"]
    return True, (f"Handoff rubric {r.overall}" + (f"; review-flagged: {flagged}" if flagged else ""))


def make_verification_gate(
    rubric_id: str = "ui_ux",
    *,
    workspace: str | Path | None = None,
    phases: tuple[str, ...] = ("REVIEW",),
    interactions: list[dict[str, Any]] | None = None,
    context: str = "",
    artifact_resolver: Callable[[Any], str | None] | None = None,
    render_fn: Callable[..., list[Screenshot]] | None = None,
    vision_judge_fn: Callable[..., VisionVerdict] | None = None,
    on_fail: Callable[[str, RubricResult], None] | None = None,
    rubric: dict[str, Any] | None = None,
) -> Callable[[str, dict[str, Any], Any], dict[str, Any]]:
    """Turn a RUBRIC into an engine gate (parallel to bakeoff.make_coding_gate's checker->gate).

    At each phase in `phases` (default REVIEW) it resolves the built-UI artifact from the
    submitted payload, runs `verify_ui_artifact`, and returns `{"pass": bool, "reason": str}`
    with a CONCRETE reason. A fail at REVIEW therefore flows through the engine's existing
    `rework_to: IMPLEMENT` loop carrying the bug description -- the loop, not a new mechanism.
    Outside `phases` it defers to the permissive `default_gate`.

    Fail-closed like make_coding_gate: a missing artifact fails (you cannot close a UI task
    without submitting what you built); an unexpected exception fails (a broken gate must not
    wave bad UI through). Graceful degradation lives one level down in verify_ui_artifact --
    an *unavailable* renderer/judge is not a failure, it's a Layer-1-only pass.

    Injectable seams (`artifact_resolver`, `render_fn`, `vision_judge_fn`, `rubric`) keep it
    unit-testable without a browser, a model, or credentials.
    """
    resolver = artifact_resolver or _default_artifact_resolver
    loaded_rubric = rubric if rubric is not None else load_rubric(rubric_id, workspace)

    def gate(phase: str, task: dict[str, Any], payload: Any) -> dict[str, Any]:
        if phase not in phases:
            return default_gate(phase, task, payload)
        artifact = resolver(payload)
        if not artifact:
            return {"pass": False,
                    "reason": (f"verification gate ({rubric_id}): no artifact to verify -- submit the built "
                               "UI path as payload['artifact'] so REVIEW can render and inspect it.")}
        try:
            result = verify_ui_artifact(
                artifact, loaded_rubric, rubric_id=rubric_id, interactions=interactions,
                context=context, render_fn=render_fn, vision_judge_fn=vision_judge_fn)
        except Exception as exc:  # noqa: BLE001 -- fail-closed, like the coding gate
            return {"pass": False, "reason": f"verification gate raised: {exc!r}"}
        if not result.passed and on_fail is not None:
            try:
                on_fail(phase, result)
            except Exception:  # noqa: BLE001 -- a KEDB-hook error must not change the verdict
                pass
        return {"pass": result.passed, "reason": result.reason}

    return gate


# ---------------------------------------------------------------------------
# Scoped composition with review_scope_gate (2026-07-07) -- visual verification is real
# per-call cost (a Playwright render + a vision-judge API call), so it must NOT run on
# every REVIEW step, only on tasks that actually produce a UI deliverable. This wraps
# `review_scope_gate` (the scope-vs-intent check) and layers the rubric verification gate
# on top, gated on a cheap, deterministic detector -- never replacing review_scope_gate's
# own tested pass/fail contract.
# ---------------------------------------------------------------------------

_UI_TASK_TYPES = frozenset({"ui", "ui_ux", "visual", "frontend"})
_UI_ENTRY_EXTS = (".html", ".htm")
_UI_COMPANION_EXTS = (".jsx", ".tsx", ".vue", ".css")


def _flagged_visual(task: dict[str, Any], payload: Any) -> bool | None:
    """Explicit opt-in/opt-out signal, checked BEFORE any file-extension heuristic: a
    `produces_ui` bool or `task_type` string set on the task, `task["intent"]`, or the
    payload. Returns True/False the moment one is found (letting a caller force the visual
    gate on OR off regardless of what files were delivered); None if nothing was set, so the
    caller falls back to the delivered-file heuristic."""
    sources: list[Any] = [task, (task or {}).get("intent") if isinstance(task, dict) else None]
    if isinstance(payload, dict):
        sources.append(payload)
    for src in sources:
        if not isinstance(src, dict):
            continue
        pu = src.get("produces_ui")
        if isinstance(pu, bool):
            return pu
        tt = src.get("task_type")
        if isinstance(tt, str) and tt.strip():
            return tt.strip().lower() in _UI_TASK_TYPES
    return None


def _delivered_file_names(payload: Any) -> list[str]:
    """Best-effort delivered-file-name list from the payload shapes this repo's callers
    already use: an explicit files list (`files`/`delivered_files`, or `scope_check.files`
    -- the same `scope_check` shape `review_scope_gate` reads), or the single artifact path
    `_default_artifact_resolver` already knows how to pull out of a REVIEW payload (walked
    recursively if it is a directory)."""
    names: list[str] = []
    if isinstance(payload, dict):
        for key in ("files", "delivered_files"):
            v = payload.get(key)
            if isinstance(v, list):
                names.extend(str(f) for f in v)
        sc = payload.get("scope_check")
        if isinstance(sc, dict) and isinstance(sc.get("files"), list):
            names.extend(str(f) for f in sc["files"])
    artifact = _default_artifact_resolver(payload)
    if artifact:
        p = Path(artifact)
        if p.is_dir():
            names.extend(str(f) for f in p.rglob("*") if f.is_file())
        else:
            names.append(str(p))
    return names


def is_visual_deliverable(task: dict[str, Any], payload: Any) -> bool:
    """The trigger condition for the scoped visual-review gate. Fails toward SKIP, not
    toward cost, by explicit user instruction -- a Playwright render + vision-judge call is
    real per-call cost that must not land on every task, only ones that actually produce UI.

    1. An explicit `produces_ui` bool or `task_type` on the task / task["intent"] / payload
       wins outright, in either direction (`_flagged_visual`).
    2. Otherwise, a file-extension heuristic over whatever delivered-file names the payload
       carries: triggers only when an HTML entry point (`.html`/`.htm`) is present --
       without one there is nothing for the Playwright renderer to open at all, so a bare
       `.css`/`.jsx` file alone does not trigger it.
    3. No signal at all -> False (skip; the safe, cost-cheap default)."""
    flagged = _flagged_visual(task if isinstance(task, dict) else {}, payload)
    if flagged is not None:
        return flagged
    names = [n.lower() for n in _delivered_file_names(payload)]
    return any(n.endswith(_UI_ENTRY_EXTS) for n in names)


def make_scoped_review_gate(
    rubric_id: str = "ui_ux",
    *,
    workspace: str | Path | None = None,
    base: GateFn = review_scope_gate,
    visual_phases: tuple[str, ...] = ("REVIEW",),
    detector: Callable[[dict[str, Any], Any], bool] = is_visual_deliverable,
    **verification_kwargs: Any,
) -> GateFn:
    """Compose `review_scope_gate` (scope-vs-intent) with the rubric visual-verification
    gate, SCOPED to tasks whose delivered payload looks like a UI artifact
    (`is_visual_deliverable`) -- not every REVIEW step. This is composition, not
    replacement: `base`'s tested pass/fail/warning contract runs first and unchanged; the
    visual layer is additional and only reachable once `base` has already passed, so a task
    can fail scope-match and separately, independently, need visual rework -- either failure
    routes back through the SAME engine `rework_to` loop `base` already uses (no new
    branching in `cortex_run_step`).

    Cost discipline (explicit user requirement): when `detector` says "not visual" this is
    EXACTLY `base` -- zero extra Playwright/vision-judge cost. Missing rubric material
    (e.g. no `calibration/rubrics/` in this workspace) degrades to a WARNING on the passing
    verdict rather than blocking the task or crashing gate construction -- graceful
    degradation is mandatory here too, same as Playwright/judge unavailability, which
    `verify_ui_artifact` already handles by passing with `judge_skipped=True`.
    """
    try:
        visual_gate: GateFn | None = make_verification_gate(
            rubric_id, workspace=workspace, phases=visual_phases, **verification_kwargs)
        load_error: str | None = None
    except Exception as exc:  # noqa: BLE001 -- e.g. no calibration/rubrics/ in this workspace
        visual_gate = None
        load_error = repr(exc)

    def gate(phase: str, task: dict[str, Any], payload: Any) -> dict[str, Any]:
        verdict = base(phase, task, payload)
        if not verdict.get("pass") or phase not in visual_phases:
            return verdict
        if not detector(task if isinstance(task, dict) else {}, payload):
            return verdict
        if visual_gate is None:
            out = dict(verdict)
            out["visual_gate_warning"] = (
                "visual deliverable detected but the rubric verification gate could not be "
                f"constructed ({load_error}) -- WARNING only, not blocking (graceful degradation).")
            return out
        visual = visual_gate(phase, task, payload)
        if not visual.get("pass"):
            return visual
        merged = dict(verdict)
        merged["visual_gate"] = visual.get("reason")
        return merged

    return gate
