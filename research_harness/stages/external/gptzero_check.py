"""CLI wrapper: run GPTZero detection on one or more review files.

Usage:
    python -m research_harness.stages.external.gptzero_check path/to/review.md [...]
    python -m research_harness.stages.external.gptzero_check path/to/review.md --cdp http://localhost:9222

Output: one JSON object per line (NDJSON), one per input file.
{
  "file": "...",
  "status": "ok" | "no_result" | "error",
  "ai_pct": float | null,
  "mixed_pct": float | null,
  "human_pct": float | null,
  "verdict": str | null,
  "confidence": str | null,
  "chars": int | null,
  "words": int | null,
  "error": str | null
}
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.request
from pathlib import Path
from typing import Any

CDP_DEFAULT = "http://localhost:9222"

_EXTRACT_JS = r"""
() => {
  const body = document.body.innerText || '';
  const lines = body.split('\n').map(l => l.trim()).filter(l => l);
  let ai=null, mixed=null, human=null;
  for (const line of lines) {
    const m = line.match(/^(AI|Mixed|Human)\s+(\d+(?:\.\d+)?)%$/i);
    if (m) {
      const k = m[1].toLowerCase();
      if (k === 'ai') ai = parseFloat(m[2]);
      else if (k === 'mixed') mixed = parseFloat(m[2]);
      else if (k === 'human') human = parseFloat(m[2]);
    }
  }
  let verdict=null, conf=null;
  const vm = body.match(/We are\s+([\w\s-]+?)\s+confident[^\n]*/i);
  if (vm) { verdict = vm[0].trim(); conf = vm[1].trim().toLowerCase(); }
  const sc = body.match(/(\d[\d,]*)\s*char/i);
  const sw = body.match(/(\d[\d,]*)\s*word/i);
  return {ai_pct: ai, mixed_pct: mixed, human_pct: human,
          verdict: verdict, confidence: conf,
          chars: sc ? parseInt(sc[1].replace(/,/g,''), 10) : null,
          words: sw ? parseInt(sw[1].replace(/,/g,''), 10) : null};
}
"""


def _get_gptzero_page(browser):
    for ctx in browser.contexts:
        for p in ctx.pages:
            try:
                if "gptzero" in p.url and "documents" in p.url:
                    return p
            except Exception:
                continue
    return None


def _open_gptzero_tab(browser) -> Any:
    ctx = browser.contexts[0] if browser.contexts else browser.new_context()
    page = ctx.new_page()
    page.goto("https://app.gptzero.me/", wait_until="domcontentloaded")
    time.sleep(4)
    # After navigation the URL should be a documents/<uuid> page
    return _get_gptzero_page(browser) or page


def scan_text(page, text: str) -> dict:
    """Inject text and run one GPTZero scan on an existing documents page."""
    # Inject via ProseMirror/Tiptap paste event
    page.click(".ProseMirror")
    time.sleep(0.3)
    page.keyboard.press("Meta+a")
    time.sleep(0.2)
    page.evaluate(
        """(text) => {
            const editor = document.querySelector('.ProseMirror');
            editor.focus();
            const dt = new DataTransfer();
            dt.setData('text/plain', text);
            editor.dispatchEvent(
                new ClipboardEvent('paste', {bubbles:true, cancelable:true, clipboardData:dt})
            );
        }""",
        text,
    )
    time.sleep(1)

    # Click the lowest visible Scan button (bottom-right of the panel)
    btns = page.evaluate(
        """() => Array.from(document.querySelectorAll('button')).map(b => ({
            text: b.innerText.trim().substring(0, 30),
            x: b.getBoundingClientRect().x,
            y: b.getBoundingClientRect().y,
            w: b.getBoundingClientRect().width,
            h: b.getBoundingClientRect().height,
        })).filter(b => b.w > 0)"""
    )
    scan_btns = [b for b in btns if "Scan" in b["text"] and b["y"] > 300]
    if not scan_btns:
        return {"status": "error", "error": "Scan button not found"}
    b = sorted(scan_btns, key=lambda x: x["y"])[-1]
    page.mouse.click(b["x"] + b["w"] / 2, b["y"] + b["h"] / 2)
    time.sleep(2)

    # Retry on transient errors
    for _ in range(3):
        try:
            ta = page.locator('button:has-text("Try again")').first
            if ta.is_visible():
                ta.click()
                time.sleep(2)
        except Exception:
            pass
        try:
            page.wait_for_function(
                "document.body.innerText.match(/We are .* confident/i)",
                timeout=20_000,
            )
            break
        except Exception:
            pass

    result = page.evaluate(_EXTRACT_JS)
    result["status"] = "ok" if result.get("ai_pct") is not None else "no_result"
    if result["status"] == "no_result":
        result["error"] = "GPTZero did not return a verdict; possibly rate-limited"
    else:
        result["error"] = None
    return result


def check_file(path: str, *, cdp_url: str = CDP_DEFAULT) -> dict:
    """Run GPTZero on a single file. Returns a result dict."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return {"file": path, "status": "error",
                "error": "playwright not installed (pip install playwright)",
                "ai_pct": None, "mixed_pct": None, "human_pct": None,
                "verdict": None, "confidence": None,
                "chars": None, "words": None}

    try:
        text = Path(path).read_text(encoding="utf-8")
    except Exception as e:
        return {"file": path, "status": "error", "error": str(e),
                "ai_pct": None, "mixed_pct": None, "human_pct": None,
                "verdict": None, "confidence": None,
                "chars": None, "words": None}

    if len(text) < 250:
        return {"file": path, "status": "error",
                "error": f"text too short ({len(text)} chars; GPTZero needs >= 250)",
                "ai_pct": None, "mixed_pct": None, "human_pct": None,
                "verdict": None, "confidence": None,
                "chars": len(text), "words": None}

    try:
        urllib.request.urlopen(cdp_url + "/json", timeout=3).read()
    except Exception as e:
        return {"file": path, "status": "error",
                "error": f"Chrome CDP not reachable at {cdp_url}: {e}",
                "ai_pct": None, "mixed_pct": None, "human_pct": None,
                "verdict": None, "confidence": None,
                "chars": None, "words": None}

    pw = sync_playwright().start()
    try:
        browser = pw.chromium.connect_over_cdp(cdp_url)
        page = _get_gptzero_page(browser)
        if page is None:
            page = _open_gptzero_tab(browser)
        if page is None:
            return {"file": path, "status": "error",
                    "error": "Could not open GPTZero documents tab; ensure you are logged in",
                    "ai_pct": None, "mixed_pct": None, "human_pct": None,
                    "verdict": None, "confidence": None,
                    "chars": None, "words": None}

        result = scan_text(page, text)
        result["file"] = path
        return result
    finally:
        try:
            pw.stop()
        except Exception:
            pass


def main():
    parser = argparse.ArgumentParser(
        description="Run GPTZero AI detection on review files."
    )
    parser.add_argument("files", nargs="+", help="Paths to review files (.md/.txt)")
    parser.add_argument(
        "--cdp", default=CDP_DEFAULT, help=f"Chrome CDP URL (default: {CDP_DEFAULT})"
    )
    parser.add_argument(
        "--delay", type=float, default=3.0,
        help="Seconds to wait between scans (default: 3.0)"
    )
    args = parser.parse_args()

    for i, path in enumerate(args.files):
        result = check_file(path, cdp_url=args.cdp)
        print(json.dumps(result), flush=True)
        if i < len(args.files) - 1:
            time.sleep(args.delay)


if __name__ == "__main__":
    main()
