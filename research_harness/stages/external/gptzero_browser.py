"""GPTZero AI-detection adapter — drives app.gptzero.me dashboard via CDP.

Why no HTTP API: GPTZero's API is paid. The free web UI is rate-limited per
IP/session but enough for our reviewer pipeline (a handful of checks per
review round).

Why no isolated Chromium: OpenProgram's sidecar Chrome (`openprogram browser
attach`, auto-bootstrapped on first use) already runs `--headless=new` by
default since commit c338077, so attaching to it gives us a background browser
that doesn't pop a window AND inherits the user's cookies/login from a copy
of their main profile. No reason to duplicate that.

Pipeline (updated 2026-05 — GPTZero redesigned their landing page):
  1. Connect to the sidecar via CDP (default http://localhost:9222).
  2. Open https://app.gptzero.me directly (requires login; the old
     gptzero.me landing page Scan now redirects here without carrying text).
  3. Locate the textarea (contenteditable div or textarea), inject text via
     the React-compatible value setter.
  4. Click the Scan button via Playwright real mouse event.
  5. Wait for the result to render in-page (confidence text appears in DOM);
     the dashboard does not navigate to a new tab.
  6. Evaluate JS to extract ai_pct, mixed_pct, human_pct, verdict,
     confidence, chars, words, ai_vocab from the current page.
"""
from __future__ import annotations

import json
import time
import urllib.request
from typing import Any, Optional


DEFAULT_CDP_URL = "http://localhost:9222"
GPTZERO_DASHBOARD = "https://app.gptzero.me/"
RESULT_URL_PREFIX = "https://app.gptzero.me/documents/"


def _list_cdp_tabs(cdp_http_base: str) -> list[dict[str, Any]]:
    """Query the CDP /json endpoint for every tab the browser knows about.

    OpenProgram's browser session only enumerates tabs it opened itself, so
    when GPTZero spawns its result tab in a *different* renderer, we have to
    bypass the tool and ask CDP directly.
    """
    url = cdp_http_base.rstrip("/") + "/json"
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=5) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    if not isinstance(data, list):
        return []
    return data


def _result_tabs(cdp_http_base: str) -> list[dict[str, Any]]:
    return [t for t in _list_cdp_tabs(cdp_http_base)
            if isinstance(t.get("url"), str)
            and t["url"].startswith(RESULT_URL_PREFIX)
            and t.get("type") == "page"]


def _find_page_by_exact_url(browser, url: str):
    """Locate a Playwright Page (across all contexts) with exactly `url`.

    Used to disambiguate the *new* GPTZero result tab from earlier result tabs
    the user may already have open — every documents/<uuid> URL is unique, so
    exact match is what we want, not prefix.
    """
    for ctx in browser.contexts:
        for page in ctx.pages:
            try:
                if page.url == url:
                    return page
            except Exception:
                continue
    return None


_INJECT_JS = r"""
(text) => {
  const ta = document.querySelector('textarea[placeholder^="Paste"]')
          || document.querySelector('textarea');
  if (!ta) return {ok: false, error: 'no_textarea'};
  // React tracks values via a hidden setter; plain assignment is ignored.
  const setter = Object.getOwnPropertyDescriptor(
    window.HTMLTextAreaElement.prototype, 'value').set;
  setter.call(ta, text);
  ta.dispatchEvent(new Event('input', {bubbles: true}));
  ta.dispatchEvent(new Event('change', {bubbles: true}));
  return {ok: true, length: ta.value.length};
}
"""


_INJECT_CONTENTEDITABLE_JS = r"""
(text) => {
  const el = document.querySelector('[contenteditable="true"]');
  if (!el) return {ok: false, error: 'no_contenteditable'};
  el.focus();
  el.innerText = text;
  el.dispatchEvent(new Event('input', {bubbles: true}));
  el.dispatchEvent(new Event('change', {bubbles: true}));
  return {ok: true, length: el.innerText.length};
}
"""

_EXTRACT_JS = r"""
() => {
  // Helpers that hunt for percentage-bearing nodes near the labels GPTZero
  // uses ("AI", "Mixed", "Human"). Falls back to regex over body text.
  const grab = () => {
    const out = {ai_pct: null, mixed_pct: null, human_pct: null};
    const text = document.body.innerText || '';
    // Pattern: "AI\n100%" or "AI 100%"
    const re = (label) => new RegExp(label + '\\s*[\\n: ]+(\\d+(?:\\.\\d+)?)\\s*%', 'i');
    let m;
    if ((m = text.match(re('AI'))))     out.ai_pct     = parseFloat(m[1]);
    if ((m = text.match(re('Mixed'))))  out.mixed_pct  = parseFloat(m[1]);
    if ((m = text.match(re('Human'))))  out.human_pct  = parseFloat(m[1]);
    return out;
  };
  const pcts = grab();

  // Verdict phrase like "We are highly confident this text was AI generated".
  const bodyText = document.body.innerText || '';
  let verdict = null;
  const vMatch = bodyText.match(
    /We are\s+([\w\s-]+?)\s+confident[^.\n]*[.\n]/i);
  if (vMatch) verdict = vMatch[0].trim();
  let confidence = null;
  if (vMatch) confidence = vMatch[1].trim().toLowerCase();

  // Stats line: "563 chars · 81 words · 6 AI Vocab" (separators vary).
  const stats = {chars: null, words: null, ai_vocab: null};
  const sChars = bodyText.match(/(\d[\d,]*)\s*char/i);
  const sWords = bodyText.match(/(\d[\d,]*)\s*word/i);
  const sVocab = bodyText.match(/(\d[\d,]*)\s*AI\s*Vocab/i);
  if (sChars) stats.chars   = parseInt(sChars[1].replace(/,/g, ''), 10);
  if (sWords) stats.words   = parseInt(sWords[1].replace(/,/g, ''), 10);
  if (sVocab) stats.ai_vocab = parseInt(sVocab[1].replace(/,/g, ''), 10);

  return {
    ai_pct: pcts.ai_pct,
    mixed_pct: pcts.mixed_pct,
    human_pct: pcts.human_pct,
    verdict: verdict,
    confidence: confidence,
    chars: stats.chars,
    words: stats.words,
    ai_vocab: stats.ai_vocab,
    url: location.href,
  };
}
"""


def check_ai_score_gptzero(
    text: str,
    *,
    cdp_url: str = DEFAULT_CDP_URL,
    result_timeout: float = 60.0,
    keep_tab: bool = False,
    cleanup_extra_gptzero_tabs: bool = True,
) -> dict[str, Any]:
    """Run GPTZero on `text` and return a structured detection result.

    Uses the app.gptzero.me dashboard directly (requires the user to be
    logged in via the attached Chrome profile).

    Args:
        text:            The text to score. Must be ≥ 250 chars.
        cdp_url:         Chrome DevTools Protocol HTTP base.
        result_timeout:  Max seconds to wait for the verdict to render.
        keep_tab:        If False (default), close the dashboard tab after
                         extracting the score.
        cleanup_extra_gptzero_tabs: Close stale GPTZero/Stripe tabs.

    Returns: {
        "status":      "ok" | "no_result" | "error",
        "ai_pct":      float | None,
        "mixed_pct":   float | None,
        "human_pct":   float | None,
        "verdict":     str | None,
        "confidence":  str | None,
        "chars":       int | None,
        "words":       int | None,
        "ai_vocab":    int | None,
        "url":         str | None,
        "error":       str | None,
    }
    """
    if len(text) < 250:
        return {"status": "error",
                "error": f"text too short ({len(text)} chars; GPTZero needs >= 250)",
                "ai_pct": None, "mixed_pct": None, "human_pct": None,
                "verdict": None, "confidence": None,
                "chars": None, "words": None, "ai_vocab": None, "url": None}

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return {"status": "error",
                "error": "playwright not installed (pip install playwright)",
                "ai_pct": None, "mixed_pct": None, "human_pct": None,
                "verdict": None, "confidence": None,
                "chars": None, "words": None, "ai_vocab": None, "url": None}

    try:
        _list_cdp_tabs(cdp_url)
    except Exception as e:
        return {"status": "error",
                "error": f"cannot reach CDP at {cdp_url}: {type(e).__name__}: {e}",
                "ai_pct": None, "mixed_pct": None, "human_pct": None,
                "verdict": None, "confidence": None,
                "chars": None, "words": None, "ai_vocab": None, "url": None}

    pw = sync_playwright().start()
    page = None
    try:
        browser = pw.chromium.connect_over_cdp(cdp_url)
        context = browser.contexts[0] if browser.contexts else browser.new_context()
        page = context.new_page()
        page.set_default_timeout(20_000)
        page.goto(GPTZERO_DASHBOARD, wait_until="domcontentloaded")

        # Wait for the editable area — dashboard uses a contenteditable div,
        # not a textarea.
        try:
            page.wait_for_selector(
                '[contenteditable="true"], textarea', timeout=15_000)
        except Exception as e:
            return _err(f"editable area never mounted: {e}", page, keep_tab)

        # Check if we're on a login wall
        body_text = page.inner_text("body")
        if "sign in" in body_text.lower() or "log in" in body_text.lower():
            if "paste" not in body_text.lower() and "scan" not in body_text.lower():
                return _err(
                    "GPTZero dashboard shows a login wall; "
                    "please log in at app.gptzero.me in the attached Chrome profile",
                    page, keep_tab, status="error")

        # Try textarea first, then contenteditable
        injected = page.evaluate(_INJECT_JS, text)
        if not (isinstance(injected, dict) and injected.get("ok")):
            # Try contenteditable div
            injected = page.evaluate(_INJECT_CONTENTEDITABLE_JS, text)
        if not (isinstance(injected, dict) and injected.get("ok")):
            return _err(f"text inject failed: {injected}", page, keep_tab)

        time.sleep(0.8)

        # Click Scan
        scan_locator = page.locator(
            'button:has-text("Scan"):not(:has-text("Advanced"))').first
        try:
            scan_locator.wait_for(state="visible", timeout=10_000)
            scan_locator.scroll_into_view_if_needed(timeout=5_000)
            scan_locator.click(timeout=10_000, force=True)
        except Exception as e:
            return _err(f"scan button click failed: {type(e).__name__}: {e}",
                        page, keep_tab)

        # Wait for verdict to render in-page (dashboard does not navigate away)
        try:
            page.wait_for_function(
                "document.body.innerText.match(/confident/i)",
                timeout=result_timeout * 1000,
            )
        except Exception:
            pass

        raw = page.evaluate(_EXTRACT_JS)
        current_url = page.url

        if not keep_tab:
            try:
                page.close()
            except Exception:
                pass
        if cleanup_extra_gptzero_tabs:
            _close_stray_gptzero_tabs(cdp_url, keep_id=None)

        if not isinstance(raw, dict) or raw.get("ai_pct") is None:
            return {"status": "no_result",
                    "error": f"extraction returned no ai_pct: {raw}",
                    "ai_pct": None, "mixed_pct": None, "human_pct": None,
                    "verdict": None, "confidence": None,
                    "chars": None, "words": None, "ai_vocab": None,
                    "url": current_url}

        return {
            "status":     "ok",
            "ai_pct":     raw.get("ai_pct"),
            "mixed_pct":  raw.get("mixed_pct"),
            "human_pct":  raw.get("human_pct"),
            "verdict":    raw.get("verdict"),
            "confidence": raw.get("confidence"),
            "chars":      raw.get("chars"),
            "words":      raw.get("words"),
            "ai_vocab":   raw.get("ai_vocab"),
            "url":        raw.get("url") or current_url,
            "error":      None,
        }

    finally:
        try:
            pw.stop()
        except Exception:
            pass


def _close_stray_gptzero_tabs(cdp_http_base: str, *, keep_id: str | None = None) -> int:
    """Close every GPTZero/Stripe iframe tab in the sidecar.

    GPTZero loads several side-iframes (app.gptzero.me/blob:..., Stripe
    billing widgets) that accumulate across many scans and hold memory.
    Called automatically at the end of each check_ai_score_gptzero call
    when cleanup_extra_gptzero_tabs=True. Returns count closed.
    """
    try:
        import urllib.request
    except ImportError:
        return 0
    closed = 0
    try:
        url = cdp_http_base.rstrip("/") + "/json"
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=3) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        if not isinstance(data, list):
            return 0
        for t in data:
            tid = t.get("id")
            if not tid or tid == keep_id:
                continue
            if t.get("type") != "page":
                continue
            u = t.get("url") or ""
            # Match any GPTZero result/blob tab + Stripe billing iframes
            # GPTZero loads (these are direct top-level pages, not embedded).
            is_gptzero = (u.startswith("https://app.gptzero.me/")
                          or u.startswith("https://gptzero.me/")
                          or u.startswith("blob:https://app.gptzero.me/")
                          or u.startswith("blob:https://gptzero.me/"))
            is_stripe_for_gptzero = (
                ("stripe.com" in u or "stripe.network" in u)
                and "gptzero" in u.lower()
            )
            if is_gptzero or is_stripe_for_gptzero:
                try:
                    urllib.request.urlopen(
                        cdp_http_base.rstrip("/") + f"/json/close/{tid}",
                        timeout=2,
                    ).read()
                    closed += 1
                except Exception:
                    pass
    except Exception:
        pass
    return closed


def _err(msg: str, page, keep: bool, *, status: str = "error") -> dict:
    if not keep:
        try:
            page.close()
        except Exception:
            pass
    return {"status": status, "error": msg,
            "ai_pct": None, "mixed_pct": None, "human_pct": None,
            "verdict": None, "confidence": None,
            "chars": None, "words": None, "ai_vocab": None, "url": None}
