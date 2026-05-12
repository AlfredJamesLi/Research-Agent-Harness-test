"""StealthWriter humanizer adapter — drives stealthwriter.ai/dashboard/humanizer
via the OpenProgram headless sidecar Chrome.

Why no API: StealthWriter sells access through its dashboard plans, not a
documented public API. The dashboard form is the cheapest way to use the
free / paid plan you already pay for.

Pipeline:
  1. Connect to sidecar Chrome via CDP. The sidecar profile must already
     hold a logged-in StealthWriter session (the dashboard URL is gated).
     If `wipe + relaunch` after a fresh sign-in didn't get carried over,
     you'll land on /sign-in and this function returns
     status='not_logged_in'.
  2. Navigate to /dashboard/humanizer in a new page.
  3. React-set the textarea value (plain .fill() doesn't trigger React).
  4. Optionally pick rewrite level (1-10) and engine (Ghost 5.1 / Ghost 4.6).
  5. Click "Humanize".
  6. Wait for the output area to populate (poll for non-empty text in the
     output container).
  7. Extract the humanized text and close the input page.
"""
from __future__ import annotations

import time
from typing import Any, Optional

DEFAULT_CDP_URL = "http://localhost:9222"
HUMANIZER_URL = "https://stealthwriter.ai/dashboard/humanizer"
SIGN_IN_URL_PREFIX = "https://stealthwriter.ai/sign-in"


_INJECT_JS = r"""
(text) => {
  const ta = document.querySelector('textarea[placeholder^="Paste"]')
          || document.querySelector('textarea');
  if (!ta) return {ok: false, error: 'no_textarea'};
  const setter = Object.getOwnPropertyDescriptor(
    window.HTMLTextAreaElement.prototype, 'value').set;
  setter.call(ta, text);
  ta.dispatchEvent(new Event('input', {bubbles: true}));
  ta.dispatchEvent(new Event('change', {bubbles: true}));
  return {ok: true, length: ta.value.length};
}
"""


# Keywords that mark UI state, FAQ, or in-progress placeholders — never
# the actual humanized output. Any candidate block matching one is dropped.
_PLACEHOLDER_NEEDLES = (
    "Humanizing your text", "This usually takes",
    "No score yet", "Click \"Check for AI\"",
    "Humanizer FAQ", "Common questions about humanizing",
    "Paste your AI-generated text",
    "Limited daily humanizations", "Limited daily scans",
    "Advanced Settings", "Heavy rewrite",
)


_EXTRACT_OUTPUT_JS = r"""
() => {
  // The humanized output sits inside a card whose innerText starts with
  // "Humanized Result", followed by toolbar buttons (Humanize More /
  // Rehumanize / Compare / Copy), then the body. We strip the toolbar
  // tokens to leave the prose.
  const cards = Array.from(document.querySelectorAll(
    'div.rounded-xl,div[class*="rounded-xl"]'));
  for (const card of cards) {
    if (!card.offsetParent) continue;
    const t = (card.innerText || '').trim();
    if (!t.startsWith('Humanized Result')) continue;
    // Strip known toolbar labels.
    const strip_tokens = [
      'Humanized Result', 'Humanize More', 'Rehumanize',
      'Deep Scan On', 'Deep Scan Off', 'Compare', 'Copy',
    ];
    let body = t;
    for (const tok of strip_tokens) {
      body = body.split(tok).join('');
    }
    body = body.replace(/^\s+/, '').replace(/\s+$/, '');
    if (body.length >= 50) return body;
  }
  return null;
}
"""


# JS check for "is humanize still in progress?". Returns true while the
# loading UI is visible.
_IN_PROGRESS_JS = r"""
() => {
  const txt = document.body.innerText || '';
  if (txt.includes("Humanizing your text")) return true;
  if (txt.includes("This usually takes")) return true;
  // Humanize button text turns into "Humanizing..." or similar while
  // the network request is in flight.
  const btn = Array.from(document.querySelectorAll('button'))
    .find(b => /humaniz/i.test(b.innerText || ''));
  if (btn) {
    const t = (btn.innerText || '').toLowerCase();
    if (t.includes('humanizing')) return true;
  }
  return false;
}
"""


def humanize_with_stealthwriter(
    text: str,
    *,
    cdp_url: str = DEFAULT_CDP_URL,
    rewrite_level: Optional[int] = None,   # 1-10; None = leave default
    engine: Optional[str] = None,           # "Ghost 5.1" | "Ghost 4.6" | None
    poll_timeout: float = 120.0,
    poll_interval: float = 2.0,
    keep_input_tab: bool = False,
) -> dict[str, Any]:
    """Humanize `text` through StealthWriter's dashboard humanizer.

    Returns: {
        "status":     "ok" | "not_logged_in" | "no_output" | "error",
        "humanized":  str | None,    # the rewritten text
        "input_chars": int,
        "output_chars": int | None,
        "url":        str | None,    # final dashboard URL
        "error":      str | None,
    }
    """
    base = {
        "status": "error", "humanized": None,
        "input_chars": len(text), "output_chars": None,
        "url": None, "error": None,
    }

    if len(text) < 50:
        return {**base, "error": f"text too short ({len(text)} chars)"}

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return {**base, "error": "playwright not installed"}

    pw = sync_playwright().start()
    try:
        browser = pw.chromium.connect_over_cdp(cdp_url)
        context = browser.contexts[0] if browser.contexts else browser.new_context()
        page = context.new_page()
        page.set_default_timeout(25_000)
        try:
            page.goto(HUMANIZER_URL, wait_until="domcontentloaded")
        except Exception as e:
            return {**base, "error": f"navigate failed: {e}"}
        page.wait_for_timeout(3500)  # SPA settle

        if page.url.startswith(SIGN_IN_URL_PREFIX):
            return {**base, "status": "not_logged_in",
                    "error": ("redirected to /sign-in — sidecar profile "
                              "missing StealthWriter cookies"),
                    "url": page.url}

        # Wait for textarea to mount.
        try:
            page.wait_for_selector('textarea', timeout=15_000)
        except Exception as e:
            return _close(base, page, keep_input_tab,
                          error=f"textarea never mounted: {e}")

        injected = page.evaluate(_INJECT_JS, text)
        if not (isinstance(injected, dict) and injected.get("ok")):
            return _close(base, page, keep_input_tab,
                          error=f"inject failed: {injected}")
        if injected.get("length") != len(text):
            return _close(base, page, keep_input_tab,
                          error=f"inject length mismatch "
                                f"({injected.get('length')} vs {len(text)})")

        # Optional level / engine selection — clicked via Playwright's real
        # click so React's onClick fires.
        if rewrite_level is not None:
            try:
                page.locator(
                    f'button:has-text("{int(rewrite_level)}")'
                ).first.click(timeout=5_000)
            except Exception:
                pass  # best effort; default level is fine
        if engine:
            try:
                page.locator(f'button:has-text("{engine}")').first.click(
                    timeout=5_000)
            except Exception:
                pass

        # Wait for the Humanize button to enable, then click.
        try:
            humanize_btn = page.locator(
                'button:has-text("Humanize")').first
            humanize_btn.wait_for(state="visible", timeout=5_000)
            for _ in range(20):                  # up to 4s for re-enable
                if not humanize_btn.is_disabled():
                    break
                page.wait_for_timeout(200)
            humanize_btn.click(timeout=10_000)
        except Exception as e:
            return _close(base, page, keep_input_tab,
                          error=f"humanize click failed: {e}")

        # Two-phase wait: first wait for in-progress UI to clear, then
        # poll for a stable, non-placeholder output block.
        deadline = time.time() + poll_timeout
        in_progress_seen = False
        while time.time() < deadline:
            if page.evaluate(_IN_PROGRESS_JS):
                in_progress_seen = True
                time.sleep(poll_interval)
                continue
            # In-progress cleared (or never observed). Now look for output.
            if not in_progress_seen:
                # Sometimes the request finishes before our first poll —
                # sleep a bit just to make sure the DOM has rendered.
                time.sleep(poll_interval)
                in_progress_seen = True
                continue
            break

        humanized = None
        last_seen_len = 0
        # Now poll for the humanized output card to render.
        ext_deadline = time.time() + 25.0
        while time.time() < ext_deadline:
            cur_text = page.evaluate(_EXTRACT_OUTPUT_JS)
            if cur_text:
                cur_len = len(cur_text)
                if cur_len == last_seen_len and cur_len > 50:
                    humanized = cur_text
                    break
                last_seen_len = cur_len
            time.sleep(poll_interval)

        if humanized is None:
            return _close(base, page, keep_input_tab, status="no_output",
                          error=f"no stable output after {poll_timeout}s")

        result = {
            **base, "status": "ok", "humanized": humanized,
            "output_chars": len(humanized), "url": page.url, "error": None,
        }
        return _close(result, page, keep_input_tab)

    finally:
        try:
            pw.stop()
        except Exception:
            pass


def _close(result: dict, page, keep: bool, *,
           status: Optional[str] = None,
           error: Optional[str] = None) -> dict:
    if not keep:
        try:
            page.close()
        except Exception:
            pass
    if status:
        result["status"] = status
    if error:
        result["error"] = error
    return result
