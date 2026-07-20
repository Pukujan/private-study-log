from __future__ import annotations

import argparse
import errno
import functools
import html
import http.client
import inspect
import ipaddress
import logging
import os
import socket
from html.parser import HTMLParser
from datetime import datetime, timezone
import json
from pathlib import Path
import re
from typing import Any
from urllib.parse import urlparse
from urllib.request import (
    HTTPHandler,
    HTTPRedirectHandler,
    HTTPSHandler,
    Request,
    build_opener,
)

# Content-negotiation for the native fetch tier: ASK for markdown/plain first (many docs sites +
# the /llms.txt ecosystem serve clean markdown when requested), fall back to HTML (-> _html_to_text).
# A real User-Agent avoids the blanket blocks some servers apply to the default python-urllib UA.
_FETCH_HEADERS = {
    "Accept": "text/markdown, text/plain;q=0.9, text/html;q=0.8, */*;q=0.5",
    "User-Agent": "CortexFetch/1.0 (+https://github.com/Pukujan/stupidly-simple-cortex)",
}

import yaml

from .config import make_stdio_encoding_safe, resolve_workspace_override

MAX_SHARD_BYTES = 500 * 1024 * 1024
ALLOWED_URL_SCHEMES = {"http", "https"}
DEFAULT_FETCH_TIMEOUT = 10
MAX_FETCH_BYTES = 10 * 1024 * 1024

# Selectable fetch backends (design: docs/research/fetch-discovery-backends-design-2026-07-06.md).
# `native` = stdlib/urllib (default, always available). `playwright` = headless-Chromium render
# for JS/SPA pages (optional `[browser]` extra). Selection: explicit `backend=` arg, else the
# CORTEX_FETCH_BACKEND env var, else `native`. An unavailable preferred backend degrades to
# native with a logged notice -- never a silent switch, never a crash.
FETCH_BACKENDS = ("native", "playwright")
DEFAULT_FETCH_BACKEND = "native"

# NAT64 well-known prefix (RFC 6052 sec. 2.1). An address inside this /96
# embeds an IPv4 address in its low 32 bits; ``ipaddress.is_global`` sees only
# the IPv6 wrapper and reports it global even when the embedded IPv4 is
# private/loopback/link-local (e.g. 64:ff9b::7f00:1 -> 127.0.0.1). R2.
_NAT64_WELL_KNOWN_PREFIX = ipaddress.ip_network("64:ff9b::/96")

# KE-02 (gate 0.17): IP-pinning direct-dials the validated origin IP, so a
# forward proxy configured via any of these never sees the connection --
# ProxyHandler is bypassed, not honored. Fail fast instead of a bare timeout.
_PROXY_ENV_VARS = ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy")

_logger = logging.getLogger(__name__)


_SLUG_MAX_LEN = 80


def _slugify(text: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", text.lower()).strip("-")
    # `name` is user-supplied (--name CLI arg / cortex_fetch_doc MCP param);
    # an unbounded slug overflows Windows' ~260-char full-path limit and
    # crashes the write after the network round-trip (same class as
    # audit._slugify, fixed 2026-07-04). Cap the same way.
    slug = slug[:_SLUG_MAX_LEN].strip("-")
    return slug or "doc"


def _shard_number(path: Path) -> int:
    match = re.search(r"cortex-(\d+)$", path.name)
    return int(match.group(1)) if match else 0


def _dir_size(path: Path) -> int:
    total = 0
    for item in path.rglob("*"):
        if item.is_file():
            total += item.stat().st_size
    return total


def _default_resolver(hostname: str) -> list[str]:
    infos = socket.getaddrinfo(hostname, None)
    return [info[4][0] for info in infos]


def _is_global_ip(ip_str: str) -> bool:
    try:
        ip = ipaddress.ip_address(ip_str)
    except ValueError:
        return False
    # NAT64 (R2): a 64:ff9b::/96 address is only as global as the IPv4 it
    # embeds in its low 32 bits. Unwrap and judge the embedded IPv4 so a
    # NAT64-encoded private/loopback/link-local target cannot masquerade as a
    # global IPv6.
    if isinstance(ip, ipaddress.IPv6Address) and ip in _NAT64_WELL_KNOWN_PREFIX:
        embedded_ipv4 = ipaddress.IPv4Address(int(ip) & 0xFFFFFFFF)
        return embedded_ipv4.is_global
    return ip.is_global


def _resolve_validated_ip(url: str, resolver: Any) -> str:
    """Resolve ``url``'s host exactly once, validate that *every* resolved
    address is global, and return the single IP the connection must be pinned
    to.

    This is the resolve-and-validate core of the SSRF host guard, but it
    *returns* the validated IP (rather than only raising) so the network path
    can dial that exact address instead of performing a second, unvalidated DNS
    lookup at connect time -- closing the DNS-rebinding TOCTOU window (R1).
    """
    parsed = urlparse(url)
    if parsed.scheme not in ALLOWED_URL_SCHEMES:
        raise ValueError(
            f"URL scheme {parsed.scheme!r} is not allowed; only http/https are permitted: {url!r}"
        )
    hostname = parsed.hostname
    if not hostname:
        raise ValueError(f"URL has no host: {url!r}")

    try:
        literal_ip = ipaddress.ip_address(hostname)
    except ValueError:
        literal_ip = None

    ips_to_check = (
        [str(literal_ip)] if literal_ip is not None else list(resolver(hostname))
    )
    if not ips_to_check:
        raise ValueError(f"could not resolve host {hostname!r}")
    for ip_str in ips_to_check:
        if not _is_global_ip(ip_str):
            raise ValueError(
                f"URL host {hostname!r} resolves to non-public address {ip_str!r}; refusing to fetch"
            )
    return ips_to_check[0]


def _validate_url(url: str, resolver: Any) -> None:
    """Raise ``ValueError`` unless ``url`` is an http(s) URL whose host resolves
    entirely to global addresses. Thin wrapper over ``_resolve_validated_ip``
    (which additionally returns the IP to pin); kept as the pre-flight /
    redirect-time guard so its call sites read intent-first."""
    _resolve_validated_ip(url, resolver)


class _PinnedConnectionMixin:
    """Mixin for ``http.client`` connections that dials a caller-validated
    ``pinned_ip`` instead of re-resolving ``self.host`` at connect time.

    ``self.host`` is left as the original hostname, so the ``Host`` header and
    (for TLS) the SNI ``server_hostname`` still derive from the real name; only
    the socket's *destination address* is overridden. ``create_connection`` is
    the socket-dialing seam (default ``socket.create_connection``, set by
    ``http.client.HTTPConnection.__init__``), injectable so tests can observe
    exactly which address the connection is told to dial.
    """

    def __init__(
        self,
        *args: Any,
        pinned_ip: str,
        create_connection: Any = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)
        self._pinned_ip = pinned_ip
        if create_connection is not None:
            # http.client.HTTPConnection.__init__ set self._create_connection to
            # socket.create_connection; override it with the injected seam.
            self._create_connection = create_connection

    def _connect_pinned_socket(self) -> None:
        # Dial the pinned IP, NOT (self.host, self.port) -- so no independent,
        # unvalidated DNS re-resolution happens between check and connect.
        self.sock = self._create_connection(
            (self._pinned_ip, self.port), self.timeout, self.source_address
        )
        try:
            self.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        except OSError as exc:
            if exc.errno != errno.ENOPROTOOPT:
                raise
        if self._tunnel_host:
            self._tunnel()


class _PinnedHTTPConnection(_PinnedConnectionMixin, http.client.HTTPConnection):
    """HTTP connection that dials the validated/pinned IP rather than a
    re-resolution of the hostname (DNS-rebinding TOCTOU hardening, R1)."""

    def connect(self) -> None:
        self._connect_pinned_socket()


class _PinnedHTTPSConnection(_PinnedConnectionMixin, http.client.HTTPSConnection):
    """HTTPS variant of :class:`_PinnedHTTPConnection`. Dials the pinned IP for
    the TCP connection, then completes the TLS handshake with the *original*
    hostname as ``server_hostname`` so SNI and certificate validation still run
    against the real name, not the pinned literal IP."""

    def connect(self) -> None:
        self._connect_pinned_socket()
        server_hostname = self._tunnel_host if self._tunnel_host else self.host
        self.sock = self._context.wrap_socket(self.sock, server_hostname=server_hostname)


class _ValidatingRedirectHandler(HTTPRedirectHandler):
    """Re-validates every redirect target before following it, so a chain
    that lands on a private/loopback/link-local address is rejected at the
    final hop, not just the initial URL."""

    def __init__(self, resolver: Any) -> None:
        super().__init__()
        self._resolver = resolver

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: N802
        _validate_url(newurl, self._resolver)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class _PinningHTTPHandler(HTTPHandler):
    """Per-request HTTP handler that resolves-and-validates the target host
    once and pins the connection to that validated IP. Because urllib routes
    every redirect hop back through ``http_open`` (via ``parent.open``), each
    hop re-resolves, re-validates, and re-pins by construction."""

    def __init__(self, resolver: Any, create_connection: Any = None) -> None:
        super().__init__()
        self._resolver = resolver
        self._pin_connector = create_connection

    def http_open(self, req):  # noqa: N802
        pinned_ip = _resolve_validated_ip(req.get_full_url(), self._resolver)
        conn_factory = functools.partial(
            _PinnedHTTPConnection,
            pinned_ip=pinned_ip,
            create_connection=self._pin_connector,
        )
        return self.do_open(conn_factory, req)


class _PinningHTTPSHandler(HTTPSHandler):
    """HTTPS counterpart of :class:`_PinningHTTPHandler`."""

    def __init__(self, resolver: Any, create_connection: Any = None) -> None:
        super().__init__()
        self._resolver = resolver
        self._pin_connector = create_connection

    def https_open(self, req):  # noqa: N802
        pinned_ip = _resolve_validated_ip(req.get_full_url(), self._resolver)
        conn_factory = functools.partial(
            _PinnedHTTPSConnection,
            pinned_ip=pinned_ip,
            create_connection=self._pin_connector,
        )
        # Python 3.12 folded check_hostname into the SSLContext and dropped the
        # handler's separate ``_check_hostname`` attribute (its own https_open now
        # passes only ``context=``). Mirror that: forward check_hostname only where the
        # stdlib still exposes it (3.10/3.11); on 3.12+ the context enforces hostname
        # verification, so the SSRF/TLS guarantee is unchanged.
        kwargs: dict[str, Any] = {"context": self._context}
        check_hostname = getattr(self, "_check_hostname", None)
        if check_hostname is not None:
            kwargs["check_hostname"] = check_hostname
        return self.do_open(conn_factory, req, **kwargs)


def _default_opener(
    url: str,
    timeout: float | None = None,
    resolver: Any = None,
    create_connection: Any = None,
) -> Any:
    """Open ``url`` with the SSRF host guard *and* IP pinning: each hop resolves
    the host once, validates it is global, and dials that exact IP -- never a
    second, unvalidated re-resolution (R1). ``create_connection`` injects the
    socket-dialing seam (default ``socket.create_connection``) so the pinned
    address is observable end-to-end in tests; production passes nothing and
    uses the real connector."""
    resolver = resolver or _default_resolver
    director = build_opener(
        _PinningHTTPHandler(resolver, create_connection),
        _PinningHTTPSHandler(resolver, create_connection),
        _ValidatingRedirectHandler(resolver),
    )
    # Pass a Request with content-negotiation + UA headers (not a bare URL) so servers can hand us
    # markdown directly; the SSRF pinning/redirect handlers operate on the Request unchanged.
    return director.open(Request(url, headers=_FETCH_HEADERS), timeout=timeout)


def _read_capped(response: Any, max_bytes: int) -> bytes:
    try:
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = response.read(65536)
            if not chunk:
                break
            total += len(chunk)
            if total > max_bytes:
                raise ValueError(f"response exceeded {max_bytes} byte cap")
            chunks.append(chunk)
        return b"".join(chunks)
    except TypeError:
        # response.read() does not accept a size argument (e.g. test doubles).
        raw = response.read()
        if len(raw) > max_bytes:
            raise ValueError(f"response exceeded {max_bytes} byte cap") from None
        return raw


def choose_doc_shard(workspace: Path) -> Path:
    docs_root = workspace / "docs"
    docs_root.mkdir(parents=True, exist_ok=True)
    shards = [p for p in docs_root.glob("cortex-*") if p.is_dir()]
    if not shards:
        shard = docs_root / "cortex-1"
        shard.mkdir(parents=True, exist_ok=True)
        return shard
    shards = sorted(shards, key=_shard_number)
    for shard in shards:
        if _dir_size(shard) < MAX_SHARD_BYTES:
            return shard
    next_shard = docs_root / f"cortex-{_shard_number(shards[-1]) + 1}"
    next_shard.mkdir(parents=True, exist_ok=True)
    return next_shard


def _update_collection_catalog(workspace: Path, name: str, source_url: str, local_path: Path) -> None:
    catalog = workspace / "library" / "cortex-library" / "sources" / "collection.yaml"
    catalog.parent.mkdir(parents=True, exist_ok=True)
    if catalog.exists():
        raw_text = catalog.read_text(encoding="utf-8")
        try:
            data = yaml.safe_load(raw_text) or {}
        except yaml.YAMLError:
            # F3/KE-06 (gate 0.5): never silently reset to {} -- that drops
            # every prior source with no recovery path. Back up the corrupt
            # bytes verbatim first, so the prior catalog is still recoverable,
            # then start fresh.
            timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            backup = catalog.with_name(f"{catalog.stem}.corrupt-{timestamp}{catalog.suffix}")
            backup.write_text(raw_text, encoding="utf-8")
            _logger.error(
                "Cortex catalog %s is not valid YAML; backed up corrupt contents to %s "
                "and starting a fresh catalog",
                catalog,
                backup,
            )
            data = {}
    else:
        data = {}
    sources = data.get("sources") or []
    local_posix = local_path.as_posix()
    if any(
        entry.get("source_url") == source_url or entry.get("local_path") == local_posix
        for entry in sources
    ):
        return
    sources.append(
        {
            "name": name,
            "source_url": source_url,
            "local_path": local_posix,
            "checked_at": datetime.now(timezone.utc).isoformat(),
        }
    )
    data["sources"] = sources
    catalog.write_text(
        yaml.safe_dump(data, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )


# --- HTML -> text extraction (ROADMAP gate 0.4, stdlib-only heuristic) ----------
# A fetched HTML page stored as raw markup is corpus poison: chunking indexes
# <script>/<style>/nav tags, drowning the actual prose and wasting the token
# budget a scope pack is supposed to protect. This is a conservative, dependency
# -free extractor: drop non-content elements, keep visible text, surface the meta
# description (for arXiv that's the abstract) up top. It is a heuristic, not a
# full HTML->markdown renderer -- good enough to make fetched pages searchable.

# Anchored at the start of the document (modulo BOM, whitespace, leading
# comments). Requiring an HTML structural tag *at the start* is what keeps a
# markdown doc that merely mentions <html> mid-prose from being mis-detected and
# mangled (review M4). `head` is deliberately NOT a skip tag: <title> lives
# inside <head>, so skipping head would drop the title (review H1); script/style
# inside head are still skipped by their own membership below.
_HTML_SNIFF = re.compile(
    r"^\ufeff?\s*(?:<!--.*?-->\s*)*(?:<!doctype\s+html|<html\b|<head\b|<body\b)",
    re.IGNORECASE | re.DOTALL,
)
_NON_CONTENT_TAGS = {"script", "style", "noscript", "template", "svg"}
_BLOCK_TAGS = {
    "p", "div", "br", "li", "tr", "section", "article", "header", "footer",
    "h1", "h2", "h3", "h4", "h5", "h6", "blockquote", "pre", "ul", "ol", "table",
}


def _looks_like_html(text: str) -> bool:
    return bool(_HTML_SNIFF.match(text))


class _HTMLTextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._parts: list[str] = []
        self._skip_depth = 0
        self.title: str | None = None
        self.meta_description: str | None = None
        self._in_title = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _NON_CONTENT_TAGS:
            self._skip_depth += 1
            return
        if tag == "title":
            self._in_title = True
        if tag == "meta":
            a = dict(attrs)
            key = (a.get("name") or a.get("property") or "").lower()
            content = (a.get("content") or "").strip()
            # Prefer og:description (on arXiv that's the real abstract) over the
            # generic name="description" ("Abstract page for arXiv paper ...").
            if content and (
                key == "og:description"
                or (key == "description" and self.meta_description is None)
            ):
                self.meta_description = content
        if tag in _BLOCK_TAGS:
            self._parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in _NON_CONTENT_TAGS and self._skip_depth > 0:
            self._skip_depth -= 1
            return
        if tag == "title":
            self._in_title = False
        if tag in _BLOCK_TAGS:
            self._parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._skip_depth > 0:
            return
        if self._in_title and self.title is None:
            self.title = data.strip() or None
        if data.strip():
            self._parts.append(data)

    def text(self) -> str:
        raw = "".join(self._parts)
        # collapse intra-line whitespace, then squeeze blank-line runs
        lines = [re.sub(r"[ \t ]+", " ", ln).strip() for ln in raw.splitlines()]
        out: list[str] = []
        blank = 0
        for ln in lines:
            if ln:
                out.append(ln)
                blank = 0
            else:
                blank += 1
                if blank <= 1:
                    out.append("")
        return "\n".join(out).strip()


def _blunt_strip(raw_html: str) -> str:
    r"""Last-resort HTML->text for malformed input. The ``</\1>|\Z`` alternation
    means an UNCLOSED <script>/<style> is consumed to end-of-string rather than
    leaving its body to leak through as prose (review L10)."""
    stripped = re.sub(r"(?is)<(script|style|head)\b.*?(?:</\1>|\Z)", " ", raw_html)
    stripped = re.sub(r"(?s)<[^>]+>", " ", stripped)
    text = html.unescape(stripped)
    lines = [re.sub(r"[^\S\n]+", " ", ln).strip() for ln in text.splitlines()]
    return "\n".join(ln for ln in lines if ln).strip()


def _html_to_text(raw_html: str) -> str:
    parser = _HTMLTextExtractor()
    try:
        parser.feed(raw_html)
        parser.close()
    except Exception:
        return _blunt_strip(raw_html)  # a malformed page must not sink the fetch
    # An unclosed <script>/<style> leaves skip active, which would otherwise
    # silently swallow the rest of the body (review M5) -- fall back to the blunt
    # strip, which handles unclosed skip tags safely.
    if parser._skip_depth != 0:
        return _blunt_strip(raw_html)
    body = parser.text()
    header: list[str] = []
    if parser.title:
        header.append(f"# {parser.title}")
    if parser.meta_description:
        header.append(f"> {parser.meta_description}")
    if header:
        return "\n\n".join(header) + "\n\n" + body
    return body


def _resolve_backend(backend: str | None) -> str:
    """Resolve the effective backend name from the explicit arg, then the
    CORTEX_FETCH_BACKEND env var, then the default. Unknown names fall back to
    native with a logged notice (never crash on a typo'd env var)."""
    chosen = (backend or os.environ.get("CORTEX_FETCH_BACKEND") or DEFAULT_FETCH_BACKEND).lower()
    if chosen not in FETCH_BACKENDS:
        _logger.warning(
            "unknown fetch backend %r (known: %s); using %r",
            chosen, ", ".join(FETCH_BACKENDS), DEFAULT_FETCH_BACKEND,
        )
        return DEFAULT_FETCH_BACKEND
    return chosen


def _native_fetch_text(url: str, opener: Any) -> str:
    """The native/urllib fetch: SSRF-pinned GET, byte-capped read, HTML->text.
    Extracted so backend dispatch (native vs playwright) is a single seam."""
    call_kwargs: dict[str, Any] = {}
    try:
        if "timeout" in inspect.signature(opener).parameters:
            call_kwargs["timeout"] = DEFAULT_FETCH_TIMEOUT
    except (TypeError, ValueError):
        pass
    with opener(url, **call_kwargs) as response:
        raw = _read_capped(response, MAX_FETCH_BYTES)
    text = raw.decode("utf-8", errors="replace")
    # Convert HTML to readable text so the corpus isn't fed raw markup (gate 0.4).
    # Only touches pages that sniff as HTML -- markdown/plain-text passes through.
    if _looks_like_html(text):
        text = _html_to_text(text)
    return text


def _fetch_text(url: str, backend: str, opener: Any, resolver: Any) -> str:
    """Dispatch to the selected backend, degrading to native when the preferred
    backend is unavailable (logged, never silent). The playwright backend
    returns rendered DOM HTML, which is run through the SAME _html_to_text
    extractor the native path uses -- no duplicated extraction logic."""
    if backend == "playwright":
        from . import browser_fetch  # local import: [browser] extra is optional
        try:
            rendered_html = browser_fetch.fetch_rendered_html(url, resolver=resolver)
            return _html_to_text(rendered_html)
        except browser_fetch.PlaywrightUnavailable as exc:
            _logger.warning(
                "playwright backend requested but unavailable (%s); degrading to native for %s",
                exc, url,
            )
            # fall through to native
    return _native_fetch_text(url, opener)


def fetch_document(
    url: str,
    name: str,
    workspace: str | Path | None = None,
    opener: Any = None,
    target_shard: str | None = None,
    resolver: Any = None,
    backend: str | None = None,
) -> Path:
    if not os.environ.get("CORTEX_ALLOW_PROXY"):
        proxy_var = next((v for v in _PROXY_ENV_VARS if os.environ.get(v)), None)
        if proxy_var:
            raise RuntimeError(
                f"{proxy_var} is set, but Cortex fetch's SSRF guard pins each connection "
                "to a validated origin IP and dials it directly, so an HTTP(S) proxy is "
                "bypassed rather than used -- fetches over a proxy-only egress path fail "
                "with a silent timeout instead of using the proxy. Set "
                "CORTEX_ALLOW_PROXY=1 to disable this check and trust your proxy's own "
                "egress controls, or unset the proxy environment variable."
            )
    resolver = resolver or _default_resolver
    # SSRF boundary guard applies to EVERY backend: validate before the native
    # GET, and (redundantly, defense-in-depth) the playwright backend re-validates
    # the top URL before launching Chromium and per-sub-request via a route guard.
    _validate_url(url, resolver)
    effective_backend = _resolve_backend(backend)

    if opener is None:
        opener = functools.partial(_default_opener, resolver=resolver)

    # Arg-first: an explicit workspace wins over CORTEX_WORKSPACE (the MCP layer already resolved
    # the tenant-pin-safe path and passes it concretely); an omitted workspace falls back env-first.
    ws = resolve_workspace_override(workspace)
    shard = Path(target_shard) if target_shard else choose_doc_shard(ws)
    shard.mkdir(parents=True, exist_ok=True)
    slug = _slugify(name)
    path = shard / f"{slug}.md"

    text = _fetch_text(url, effective_backend, opener, resolver)
    fetched_at = datetime.now(timezone.utc).isoformat()
    if not text.lstrip().startswith("---"):
        text = (
            "---\n"
            f"source_url: {json.dumps(url)}\n"
            f"fetched_at: {json.dumps(fetched_at)}\n"
            "---\n\n"
            f"# {name}\n\n"
            f"Source: {url}\n\n"
            + text
        )
    path.write_text(text, encoding="utf-8")
    _update_collection_catalog(ws, slug, url, path)
    return path


def main(argv: list[str] | None = None) -> int:
    make_stdio_encoding_safe()
    parser = argparse.ArgumentParser(description="Fetch a document into a Cortex shard")
    parser.add_argument("--url", required=True)
    parser.add_argument("--name", required=True)
    parser.add_argument("--workspace", default=None)
    parser.add_argument(
        "--backend",
        default=None,
        choices=FETCH_BACKENDS,
        help="fetch backend: native (default, urllib) or playwright (headless-Chromium "
        "render for JS/SPA pages; requires the [browser] extra, degrades to native if absent). "
        "Defaults to $CORTEX_FETCH_BACKEND or native.",
    )
    args = parser.parse_args(argv)
    path = fetch_document(args.url, args.name, workspace=args.workspace, backend=args.backend)
    print(path)
    return 0
