"""Playwright (headless Chromium) fetch backend -- the optional ``[browser]``
extra of the selectable fetch backends designed in
``docs/research/fetch-discovery-backends-design-2026-07-06.md``.

Why this exists: ``fetch.py``'s native/urllib backend performs a plain HTTP GET,
so a JS-rendered SPA (carbondesignsystem.com, atlassian.design, zeroheight blog
posts -- observed 2026-07-07) returns only its pre-render shell, not the prose.
This backend drives a real headless browser so the page's JS runs and the
rendered DOM is what we extract.

SSRF stance (the part most likely to be gotten wrong for a browser engine)
==========================================================================
A headless browser fetching arbitrary URLs is a *larger* SSRF surface than
urllib, not smaller: ``page.goto`` does its own DNS resolution and socket
connect, and the loaded page can then issue *its own* sub-requests (img/xhr/
fetch/css) to whatever hosts it likes -- each an independent egress. We defend
on two layers, both reusing the native backend's exact ``_is_global_ip`` /
``_resolve_validated_ip`` rule (global-IP-only, no private/loopback/link-local/
NAT64-embedded-private, http(s)-only):

  1. **Top-URL boundary check, before the browser even launches.** We call
     ``_resolve_validated_ip(url)`` first; a private/loopback/scheme-illegal
     target raises ``ValueError`` and no browser process is ever started.

  2. **Per-request route guard, for every sub-resource.** ``page.route`` sees
     every network request the page makes; we resolve+validate each http(s)
     request's host and ``route.abort()`` any that resolves to a non-global
     address. Non-network schemes (data:/blob:/about:) carry no host egress and
     are allowed through so rendering isn't needlessly broken.

**Honest residual limitation (documented, not hidden):** the native backend
*pins* the validated IP into the socket (``_PinnedHTTPConnection``), closing the
DNS-rebinding TOCTOU window. Playwright does not expose a socket-level connect
seam, so the route guard *re-resolves* and validates but cannot guarantee the
browser then dials that same IP -- a low-TTL rebinding attacker retains a narrow
check-vs-connect window here that the native backend does not have. This backend
is therefore strictly for deliberately ingesting a *known* JS-heavy public doc
source, not for pointing at attacker-controlled hostnames. See the design doc.
"""

from __future__ import annotations

import logging
from urllib.parse import urlparse

from .fetch import (
    DEFAULT_FETCH_TIMEOUT,
    _FETCH_HEADERS,
    _default_resolver,
    _resolve_validated_ip,
)

_logger = logging.getLogger(__name__)


class PlaywrightUnavailable(RuntimeError):
    """Raised when the ``playwright`` package (or its Chromium browser) is not
    installed. Callers degrade to the native backend rather than crash."""


def playwright_available() -> bool:
    """True if the ``playwright`` Python package can be imported. This does NOT
    guarantee the Chromium browser binary is installed (``playwright install
    chromium``); a launch failure for a missing browser surfaces at fetch time
    as :class:`PlaywrightUnavailable`."""
    try:
        import playwright.sync_api  # noqa: F401
    except Exception:
        return False
    return True


def _host_is_global(url: str, resolver) -> bool:
    """True iff ``url``'s host resolves entirely to global addresses under the
    native backend's exact rule. Never raises -- a validation failure (private
    range, illegal scheme, unresolvable) is reported as ``False`` so the route
    guard can simply ``abort``."""
    try:
        _resolve_validated_ip(url, resolver)
    except ValueError:
        return False
    return True


def fetch_rendered_html(
    url: str,
    resolver=None,
    timeout: float = DEFAULT_FETCH_TIMEOUT,
) -> str:
    """Render ``url`` in headless Chromium and return the post-JS DOM HTML.

    SSRF: validates the top URL at OUR boundary *before* launching the browser
    (raises ``ValueError`` for a non-global/illegal target), and installs a
    per-request route guard that aborts any sub-request to a non-global host.

    Raises :class:`PlaywrightUnavailable` if playwright/Chromium is not
    installed, so the caller can degrade to the native backend.
    """
    resolver = resolver or _default_resolver

    # Layer 1 -- boundary check BEFORE any browser process is spawned. Placed
    # ahead of the playwright import so an SSRF-illegal target is rejected even
    # in an environment where playwright isn't installed at all.
    _resolve_validated_ip(url, resolver)

    try:
        from playwright.sync_api import sync_playwright
    except Exception as exc:  # ImportError, or a broken partial install
        raise PlaywrightUnavailable(
            "the [browser] extra (playwright) is not installed; "
            "`pip install -e .[browser] && playwright install chromium`"
        ) from exc

    timeout_ms = int(timeout * 1000)

    def _guard(route, request):
        parsed = urlparse(request.url)
        if parsed.scheme in ("http", "https"):
            if _host_is_global(request.url, resolver):
                route.continue_()
            else:
                _logger.warning(
                    "browser backend blocked non-global sub-request %s", request.url
                )
                route.abort()
        else:
            # data:/blob:/about:/filesystem: -- no host egress, safe to allow.
            route.continue_()

    try:
        with sync_playwright() as p:
            try:
                browser = p.chromium.launch(headless=True)
            except Exception as exc:  # Chromium binary missing / launch failure
                raise PlaywrightUnavailable(
                    "playwright is installed but Chromium failed to launch; "
                    "run `playwright install chromium`"
                ) from exc
            try:
                context = browser.new_context(
                    user_agent=_FETCH_HEADERS["User-Agent"],
                )
                page = context.new_page()
                page.route("**/*", _guard)
                # networkidle gives the SPA time to fetch+render; fall back to the
                # DOM we have if idle never settles within the timeout.
                try:
                    page.goto(url, wait_until="networkidle", timeout=timeout_ms)
                except Exception as exc:  # noqa: BLE001 -- timeout/nav error
                    _logger.warning(
                        "browser navigation to %s did not reach networkidle (%s); "
                        "returning DOM captured so far",
                        url,
                        exc,
                    )
                return page.content()
            finally:
                browser.close()
    except PlaywrightUnavailable:
        raise
    except Exception as exc:  # noqa: BLE001
        raise PlaywrightUnavailable(
            f"playwright render of {url!r} failed: {exc}"
        ) from exc
