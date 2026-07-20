"""Trace / closeout credential redaction (gap J7). Pure stdlib; no model, no network.

Full transcripts and closeouts can hold credentials / PII / source. This module is the
capture-boundary control: it scrubs the SAME credential shapes as the browser-bridge
redactor (`D:\\claude\\chrome-extension\\redact.mjs`, itself ported from
`hermes-agent/agent/redact.py`) -- vendor-prefixed API keys, JWTs, PEM private keys, auth /
secret headers, DB connection-string passwords, bare-token URLs, cookies, SSNs, card numbers,
and a Shannon-entropy fallback for opaque high-entropy tokens -- plus the repo's own
ALL-CAPS key convention (`X-LIT-Y`, matching `ops/secret_audit.py`).

Safety property (J7): `redact_trace` runs BEFORE persistence (`redact_then_persist`), so the
durable store never contains the secret to leak. `find_secrets` re-scans a store to prove the
redaction held (used by `ops/trace_store_audit.py`).
"""
from __future__ import annotations

import json
import math
import re
from pathlib import Path
from typing import Any

REDACTED = "«redacted»"  # «redacted»

# --------------------------------------------------------------------------- known-shape patterns
# Vendor-prefixed keys (ported from redact.mjs PREFIX_PATTERNS).
_PREFIX_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"sk-[A-Za-z0-9_-]{10,}"),            # OpenAI / OpenRouter / Anthropic (sk-ant-*)
    re.compile(r"ghp_[A-Za-z0-9]{10,}"),             # GitHub PAT (classic)
    re.compile(r"github_pat_[A-Za-z0-9_]{10,}"),     # GitHub PAT (fine-grained)
    re.compile(r"gh[ours]_[A-Za-z0-9]{10,}"),        # GitHub OAuth / user / server / refresh
    re.compile(r"xapp-\d+-[A-Za-z0-9-]{10,}"),       # Slack app-level token
    re.compile(r"xox[baprs]-[A-Za-z0-9-]{10,}"),     # Slack bot/app/user tokens
    re.compile(r"AIza[A-Za-z0-9_-]{30,}"),           # Google API keys
    re.compile(r"AKIA[A-Z0-9]{16}"),                 # AWS Access Key ID
    re.compile(r"sk_(?:live|test)_[A-Za-z0-9]{10,}"),# Stripe secret key
    re.compile(r"rk_live_[A-Za-z0-9]{10,}"),         # Stripe restricted key
    re.compile(r"SG\.[A-Za-z0-9_-]{10,}"),           # SendGrid
    re.compile(r"hf_[A-Za-z0-9]{10,}"),              # HuggingFace
    re.compile(r"npm_[A-Za-z0-9]{10,}"),             # npm token
    re.compile(r"pypi-[A-Za-z0-9_-]{10,}"),          # PyPI token
    re.compile(r"gsk_[A-Za-z0-9]{10,}"),             # Groq
    re.compile(r"xai-[A-Za-z0-9]{30,}"),             # xAI
    re.compile(r"ntn_[A-Za-z0-9]{10,}"),             # Notion
    re.compile(r"gAAAA[A-Za-z0-9_=-]{20,}"),         # Fernet / Codex-style encrypted tokens
    # Repo-native ALL-CAPS convention (parity with ops/secret_audit.py's "api-key-ish").
    re.compile(r"\b[A-Z0-9]{2,}-(?:LIT|SECRET|PRIV|TOKEN)-[A-Z0-9]{2,}\b"),
]

_JWT_RE = re.compile(r"eyJ[A-Za-z0-9_-]{10,}(?:\.[A-Za-z0-9_=-]{4,}){0,2}")
_PRIVATE_KEY_RE = re.compile(
    r"-----BEGIN[A-Z ]*PRIVATE KEY-----[\s\S]*?-----END[A-Z ]*PRIVATE KEY-----")
_AUTH_HEADER_RE = re.compile(
    r"((?:Proxy-)?Authorization:\s*)([A-Za-z][\w.+-]*\s+)?([^\s\"']+)", re.IGNORECASE)
_SECRET_HEADER_RE = re.compile(
    r"((?:x-api-key|x-goog-api-key|api-key|apikey|x-api-token|x-auth-token|x-access-token)"
    r"\s*:\s*)(\S+)", re.IGNORECASE)
_DB_CONNSTR_RE = re.compile(
    r"((?:postgres(?:ql)?|mysql|mongodb(?:\+srv)?|redis|amqp)://[^:\s]+:)([^@\s]+)(@)",
    re.IGNORECASE)
_URL_BARE_TOKEN_RE = re.compile(
    r"((?:https?|wss?|git|ssh|ftp|ftps|sftp)://)([^\s:@/]{8,})(@[^\s]+)", re.IGNORECASE)
_CARD_NUMBER_RE = re.compile(r"\b(?:\d[ -]?){13,19}\b")
_SSN_RE = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")

SENSITIVE_KEY_NAMES = {
    "access_token", "refresh_token", "id_token", "token", "api_key", "apikey", "client_secret",
    "password", "passwd", "auth", "jwt", "session", "sessionid", "sid", "connect.sid",
    "__session", "ssid", "secret", "key", "code", "signature", "csrftoken", "csrf_token",
    "authorization", "private_key",
}

_SECRET_FIELD_NAME_RE = re.compile(
    r"^(?:.*_)?(password|passwd|token|secret|api[_-]?key|auth|credential|cookie|otp|"
    r"one[_-]?time[_-]?code|private[_-]?key)(?:_.*)?$", re.IGNORECASE)

_COOKIE_KV_RE = re.compile(r"([A-Za-z0-9_.\-]+)=([^;\n]+)")
_HIGH_ENTROPY_TOKEN_RE = re.compile(r"\S{24,}")


# --------------------------------------------------------------------------- helpers
def _mask_token(token: str, head: int = 4, tail: int = 4, floor: int = 12) -> str:
    if not token:
        return REDACTED
    if len(token) < floor:
        return REDACTED
    return f"{token[:head]}...{token[-tail:]}"


def shannon_entropy(s: str) -> float:
    """Shannon entropy in bits/char."""
    if not s:
        return 0.0
    counts: dict[str, int] = {}
    for ch in s:
        counts[ch] = counts.get(ch, 0) + 1
    n = len(s)
    return -sum((c / n) * math.log2(c / n) for c in counts.values())


def looks_high_entropy_secret(token: str) -> bool:
    """Fallback for opaque secrets that match no known vendor prefix: long token-shaped strings
    (base64/hex/url-safe alphabet) with high character-level entropy. Conservative on length to
    avoid flagging ordinary words. A false positive (masking a non-secret) is acceptable; a
    false negative on a real credential is not."""
    if not token or len(token) < 24 or len(token) > 512:
        return False
    if not re.fullmatch(r"[A-Za-z0-9+/_=.-]+", token):
        return False
    return shannon_entropy(token) >= 3.6


# --------------------------------------------------------------------------- text redaction
def redact_text(text: Any) -> Any:
    """Redact known-shape secrets from a plain string. Non-matching input passes through
    unchanged; non-strings are returned as-is."""
    if text is None or not isinstance(text, str) or not text:
        return text

    for pattern in _PREFIX_PATTERNS:
        text = pattern.sub(lambda m: _mask_token(m.group(0), 4, 4, 10), text)

    text = _JWT_RE.sub(lambda m: _mask_token(m.group(0), 6, 4, 18), text)
    text = _PRIVATE_KEY_RE.sub("[REDACTED PRIVATE KEY]", text)

    if "uthorization" in text:
        text = _AUTH_HEADER_RE.sub(
            lambda m: f"{m.group(1)}{m.group(2) or ''}{_mask_token(m.group(3))}", text)
    text = _SECRET_HEADER_RE.sub(lambda m: f"{m.group(1)}{_mask_token(m.group(2))}", text)

    if "://" in text:
        text = _DB_CONNSTR_RE.sub(lambda m: f"{m.group(1)}***{m.group(3)}", text)
        text = _URL_BARE_TOKEN_RE.sub(
            lambda m: f"{m.group(1)}{_mask_token(m.group(2))}{m.group(3)}", text)

    # Cookie-string shape "name=value; name2=value2": redact values of sensitive-named keys,
    # leave benign ones (locale=en-US) readable so the trace isn't a total blackout.
    if "=" in text and (";" in text or re.fullmatch(r"[\w.-]+=[^;]+", text.strip() or "x")):
        def _cookie(m: re.Match[str]) -> str:
            key, value = m.group(1), m.group(2)
            if key.lower() in SENSITIVE_KEY_NAMES:
                return f"{key}={_mask_token(value.strip())}"
            return m.group(0)
        text = _COOKIE_KV_RE.sub(_cookie, text)

    text = _SSN_RE.sub("***-**-****", text)

    def _card(m: re.Match[str]) -> str:
        digits = re.sub(r"[ -]", "", m.group(0))
        if len(digits) < 13 or len(digits) > 19:
            return m.group(0)
        return f"{'*' * (len(digits) - 4)}{digits[-4:]}"
    text = _CARD_NUMBER_RE.sub(_card, text)

    if _HIGH_ENTROPY_TOKEN_RE.search(text):
        text = _HIGH_ENTROPY_TOKEN_RE.sub(
            lambda m: _mask_token(m.group(0)) if looks_high_entropy_secret(m.group(0)) else m.group(0),
            text)

    return text


# --------------------------------------------------------------------------- structured redaction
def redact_trace(value: Any, depth: int = 0) -> Any:
    """Recursively redact a trace/closeout value (str / number / list / dict).

    Object keys that look like secret field names (`password`, `token`, `api_key`, `cookie`, ...)
    have their WHOLE value replaced regardless of content; every remaining string leaf runs
    through `redact_text`. This is the primary J7 pass -- call it BEFORE persistence."""
    if depth > 16:
        return REDACTED
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, (int, float)):
        return value
    if isinstance(value, (list, tuple)):
        return [redact_trace(v, depth + 1) for v in value]
    if isinstance(value, dict):
        out: dict = {}
        for key, val in value.items():
            k = str(key)
            if _SECRET_FIELD_NAME_RE.match(k) or k.lower() in SENSITIVE_KEY_NAMES:
                out[key] = REDACTED if isinstance(val, (str, int, float)) else redact_trace(val, depth + 1)
            else:
                out[key] = redact_trace(val, depth + 1)
        return out
    return value


# --------------------------------------------------------------------------- audit / persistence
def find_secrets(value: Any, *, include_entropy: bool = False) -> list[str]:
    """Return short evidence snippets for any residual credential shape in `value` (a scan, not a
    scrub). Empty list == clean. Used by `ops/trace_store_audit.py` to prove a store never
    persisted an unredacted secret.

    HIGH-PRECISION by default: only STRUCTURED, high-confidence shapes (vendor-prefixed keys,
    JWT, PEM, SSN) are reported, so the auditor doesn't fire on every git-sha / long file path.
    The loose Shannon-entropy fallback is a REDACTION conservatism (over-masking is safe) but a
    poor GATE signal, so it is off here unless `include_entropy=True` is explicitly requested."""
    hits: list[str] = []
    scanners: list[re.Pattern[str]] = [
        *_PREFIX_PATTERNS, _JWT_RE, _PRIVATE_KEY_RE, _SSN_RE,
    ]

    def _plausible(tok: str) -> bool:
        # Real API keys / JWTs / AWS ids / SSNs all contain a digit; the loose `sk-<word>`
        # and bare `PROM-LIT-KEY` shapes that pepper English closeouts do not. Requiring a
        # digit keeps the auditor high-precision on the real corpus without missing an actual
        # high-entropy credential (which effectively always contains digits). PEM blocks are
        # matched by fixed marker text and are exempt from this filter.
        return any(c.isdigit() for c in tok)

    def _scan_str(s: str) -> None:
        for rx in scanners:
            for m in rx.finditer(s):
                tok = m.group(0)
                if rx is _PRIVATE_KEY_RE or _plausible(tok):
                    hits.append(tok[:24])
        if include_entropy:
            for tok in _HIGH_ENTROPY_TOKEN_RE.findall(s):
                if looks_high_entropy_secret(tok):
                    hits.append(tok[:24])

    def _walk(v: Any) -> None:
        if isinstance(v, str):
            _scan_str(v)
        elif isinstance(v, dict):
            for vv in v.values():
                _walk(vv)
        elif isinstance(v, (list, tuple)):
            for vv in v:
                _walk(vv)
    _walk(value)
    return hits


def redact_then_persist(record: Any, path: str | Path, *, mode: str = "a") -> Any:
    """The J7 safety guarantee, enforced mechanically: redact FIRST, then append the scrubbed
    record to `path` as one JSONL line. The durable store therefore never contains the secret.
    Returns the scrubbed record (already written)."""
    scrubbed = redact_trace(record)
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open(mode, encoding="utf-8") as fh:
        fh.write(json.dumps(scrubbed, ensure_ascii=False) + "\n")
    return scrubbed
