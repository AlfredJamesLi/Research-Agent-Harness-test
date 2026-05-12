"""Wiki helpers — deterministic Python utilities.

Slug generation, arXiv metadata fetch, frontmatter parse/dump, wikilink
rewriting, vault folder-tree listing, git commit. Kept here so the
agentic functions stay focused on prompts.

When ``wiki_agent_harness`` is installed the shared utilities
(frontmatter, wikilinks, folder_tree, git, find_node, iter_md_files)
are imported from there to avoid duplication.  Research-specific
helpers (slugify, arXiv fetch/download) always use the local
implementation.
"""

from __future__ import annotations

import re
import subprocess
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Optional

# ---------------------------------------------------------------------------
# Shared utilities — delegate to wiki_agent_harness when available
# ---------------------------------------------------------------------------
try:
    from wiki_agent_harness.helpers import (  # noqa: F401
        parse_frontmatter,
        dump_frontmatter,
        folder_tree,
        iter_md_files,
        find_node,
        rewrite_wikilinks,
    )
    _WAH_HELPERS = True
except ImportError:
    _WAH_HELPERS = False

_ARXIV_API = "http://export.arxiv.org/api/query?id_list={ids}"
_ARXIV_NS = {
    "atom": "http://www.w3.org/2005/Atom",
    "arxiv": "http://arxiv.org/schemas/atom",
}

_STOP_WORDS = frozenset({
    "a", "an", "the", "of", "for", "in", "on", "with", "via",
    "and", "to", "by", "is", "are", "as", "from",
})


# ---------------------------------------------------------------------------
# Slug
# ---------------------------------------------------------------------------

def slugify(title: str, author_last: str = "", year: int = 0) -> str:
    """`<author><year>_<keyword>` — e.g. `vaswani2017_attention_is_all`."""
    words = re.sub(r"[^a-z0-9\s]", "", title.lower()).split()
    keywords = [w for w in words if w not in _STOP_WORDS and len(w) > 2]
    keyword = "_".join(keywords[:3]) if keywords else "untitled"
    author = re.sub(r"[^a-z]", "", author_last.lower()) if author_last else "unknown"
    yr = str(year) if year else "0000"
    return f"{author}{yr}_{keyword}"


def last_name(full_name: str) -> str:
    parts = full_name.strip().split()
    return parts[-1] if parts else ""


# ---------------------------------------------------------------------------
# arXiv
# ---------------------------------------------------------------------------

def normalize_arxiv_id(arxiv_id: str) -> str:
    s = arxiv_id.strip()
    for prefix in ("arXiv:", "arxiv:", "http://arxiv.org/abs/", "https://arxiv.org/abs/"):
        if s.lower().startswith(prefix.lower()):
            s = s[len(prefix):]
    return re.sub(r"v\d+$", "", s)


def fetch_arxiv_metadata(arxiv_id: str, timeout: float = 30.0) -> dict:
    """Query arXiv Atom API. Raises RuntimeError on network or parse failure."""
    aid = normalize_arxiv_id(arxiv_id)
    url = _ARXIV_API.format(ids=aid)
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            body = resp.read()
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise RuntimeError(f"arXiv fetch failed for {aid}: {e}")

    try:
        root = ET.fromstring(body)
    except ET.ParseError as e:
        raise RuntimeError(f"arXiv returned unparseable XML for {aid}: {e}")

    entry = root.find("atom:entry", _ARXIV_NS)
    if entry is None:
        raise RuntimeError(f"arXiv returned no entry for {aid}")

    def _txt(el, default=""):
        return el.text.strip() if el is not None and el.text else default

    title = re.sub(r"\s+", " ", _txt(entry.find("atom:title", _ARXIV_NS)))
    summary = re.sub(r"\s+", " ", _txt(entry.find("atom:summary", _ARXIV_NS)))
    published = _txt(entry.find("atom:published", _ARXIV_NS))
    year = int(published[:4]) if published[:4].isdigit() else 0

    authors = []
    for a in entry.findall("atom:author", _ARXIV_NS):
        n = _txt(a.find("atom:name", _ARXIV_NS))
        if n:
            authors.append(n)

    primary = entry.find("arxiv:primary_category", _ARXIV_NS)
    primary_cat = primary.get("term") if primary is not None else ""
    journal_ref = _txt(entry.find("arxiv:journal_ref", _ARXIV_NS))
    venue = journal_ref if journal_ref else "arXiv"

    return {
        "arxiv_id": aid,
        "title": title,
        "authors": authors,
        "year": year,
        "venue": venue,
        "abstract": summary,
        "primary_category": primary_cat,
    }


def download_arxiv_pdf(arxiv_id: str, dest: Path, timeout: float = 30.0) -> bool:
    """Best-effort PDF download. Returns True on success, False otherwise.

    Skips download (returns True) if dest already exists.
    """
    if dest.exists() and dest.stat().st_size > 20_000:
        return True
    aid = normalize_arxiv_id(arxiv_id)
    url = f"https://arxiv.org/pdf/{aid}.pdf"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            data = resp.read()
        if not data.startswith(b"%PDF"):
            return False
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
        return True
    except (urllib.error.URLError, TimeoutError, OSError):
        return False


# ---------------------------------------------------------------------------
# Shared utilities — only define locally when wiki_agent_harness is absent
# ---------------------------------------------------------------------------

if not _WAH_HELPERS:
    def yaml_quote(s) -> str:
        if s is None:
            return '""'
        s = str(s).replace("\r", "").replace("\t", " ")
        s = s.replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ")
        return f'"{s}"'

    def parse_frontmatter(text: str) -> tuple[dict, str]:
        """Return (frontmatter_dict, body). Empty dict if no frontmatter."""
        m = re.match(r"^---\n(.*?)\n---\n?(.*)$", text, re.DOTALL)
        if not m:
            return {}, text
        raw, body = m.group(1), m.group(2)
        fm: dict = {}
        current_key: Optional[str] = None
        for line in raw.split("\n"):
            if not line.strip():
                continue
            if line.startswith("  - ") or line.startswith("- "):
                if current_key is None:
                    continue
                val = line.lstrip(" -").strip().strip('"')
                fm.setdefault(current_key, [])
                if isinstance(fm[current_key], list):
                    fm[current_key].append(val)
                continue
            m2 = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)\s*:\s*(.*)$", line)
            if not m2:
                continue
            key, value = m2.group(1), m2.group(2).strip()
            if not value:
                fm[key] = []
                current_key = key
            else:
                current_key = None
                if value.startswith("[") and value.endswith("]"):
                    inner = value[1:-1].strip()
                    fm[key] = [
                        v.strip().strip('"') for v in inner.split(",") if v.strip()
                    ] if inner else []
                else:
                    fm[key] = value.strip('"')
        return fm, body

    def dump_frontmatter(fm: dict, body: str) -> str:
        """Serialize frontmatter dict + body into a full page string."""
        lines = ["---"]
        for k, v in fm.items():
            if isinstance(v, list):
                if not v:
                    lines.append(f"{k}: []")
                else:
                    lines.append(f"{k}:")
                    for item in v:
                        lines.append(f"  - {yaml_quote(item)}")
            elif isinstance(v, (int, float)):
                lines.append(f"{k}: {v}")
            else:
                lines.append(f"{k}: {yaml_quote(v)}")
        lines.append("---")
        if not body.startswith("\n"):
            lines.append("")
        return "\n".join(lines) + body

    _IGNORED_DIRS = frozenset({".git", "Attachments", ".obsidian", "__pycache__"})

    def iter_md_files(root: Path):
        """Yield every .md file under root, skipping known non-content dirs."""
        for p in root.rglob("*.md"):
            if any(part in _IGNORED_DIRS for part in p.parts):
                continue
            yield p

    def folder_tree(root: Path, max_depth: int = 10) -> str:
        """Return a path-only tree (no .md content) for LLM context."""
        lines: list[str] = []
        root = root.resolve()
        for d in sorted(root.rglob("*")):
            if not d.is_dir():
                continue
            if any(part in _IGNORED_DIRS for part in d.parts):
                continue
            rel = d.relative_to(root)
            depth = len(rel.parts)
            if depth > max_depth:
                continue
            same_named = d / f"{d.name}.md"
            if same_named.exists():
                lines.append(f"{'  ' * (depth - 1)}{d.name}/")
        return "\n".join(lines)

    def find_node(root: Path, filename_stem: str) -> Optional[Path]:
        """Find the `<stem>/<stem>.md` page anywhere in the vault."""
        for d in root.rglob(filename_stem):
            if d.is_dir() and (d / f"{filename_stem}.md").exists():
                return d / f"{filename_stem}.md"
        return None

    _WIKILINK_RE = re.compile(r"\[\[([^\[\]\|#]+?)(\|[^\[\]]+)?(#[^\[\]]+)?\]\]")

    def rewrite_wikilinks(root: Path, old_stem: str, new_stem: str) -> int:
        """Rewrite every ``[[old_stem...]]`` to ``[[new_stem...]]`` across the vault."""
        changed = 0
        for path in iter_md_files(root):
            text = path.read_text()

            def _sub(m: re.Match) -> str:
                target = m.group(1).strip()
                if target != old_stem:
                    return m.group(0)
                alias = m.group(2) or ""
                anchor = m.group(3) or ""
                return f"[[{new_stem}{alias}{anchor}]]"

            new_text = _WIKILINK_RE.sub(_sub, text)
            if new_text != text:
                path.write_text(new_text)
                changed += 1
        return changed

else:
    # yaml_quote is research-internal (used by dump_frontmatter above);
    # define it here even when WAH is present, as a local helper.
    def yaml_quote(s) -> str:  # type: ignore[misc]
        if s is None:
            return '""'
        s = str(s).replace("\r", "").replace("\t", " ")
        s = s.replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ")
        return f'"{s}"'


# Git helpers — always available regardless of wiki_agent_harness
def git_run(root: Path, *args: str, check: bool = False) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(root), *args],
        capture_output=True, text=True, check=check,
    )


def ensure_git_repo(root: Path) -> None:
    if (root / ".git").exists():
        return
    git_run(root, "init", check=True)


def git_commit_all(root: Path, message: str) -> bool:
    """Stage everything under root and commit. Returns True if a commit was made."""
    ensure_git_repo(root)
    git_run(root, "add", "-A")
    status = git_run(root, "status", "--porcelain")
    if not status.stdout.strip():
        return False
    git_run(root, "commit", "-m", message)
    return True


# ---------------------------------------------------------------------------
# PDF figure extraction — caption-anchored bbox crop
# ---------------------------------------------------------------------------
def extract_pdf_figure(
    pdf_path: Path | str,
    caption_prefix: str,
    out_path: Path | str,
    *,
    include_caption: bool = True,
    dpi: int = 300,
    page_hint: int | None = None,
    max_caption_lines: int = 6,
    margin_pt: float = 4.0,
) -> tuple[int, tuple[float, float, float, float]]:
    """Crop one figure from a PDF, anchored on its caption.

    Academic papers compose figures as vector graphics + text, so
    `page.get_images()` returns at best a logo or legend swatch. This
    helper instead locates the caption block (e.g. ``"Figure 2:"``) and
    walks up the page to find the figure body, then renders that bbox
    at `dpi`.

    Args:
        pdf_path: PDF file path.
        caption_prefix: literal text that opens the caption — typically
            ``"Figure 2:"`` or ``"Figure 2 |"``. Matched as a prefix on
            stripped text blocks; the first match across pages wins.
        out_path: PNG output path.
        include_caption: whether to include the caption block in the
            crop (default True — gives the reader figure context). False
            crops only the figure body.
        dpi: render resolution.
        page_hint: optional 1-indexed page to search first. If None,
            scan all pages.
        max_caption_lines: maximum text-block lines that count as part
            of the caption (longer following blocks are body text).
        margin_pt: padding added on all sides of the cropped bbox.

    Returns:
        (page_number_1indexed, bbox) of the crop that was rendered.

    Raises:
        ValueError: caption not found.
        ImportError: pymupdf not installed.
    """
    import fitz  # type: ignore

    pdf_path = Path(pdf_path)
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    doc = fitz.open(pdf_path)
    target = caption_prefix.strip()

    pages_order = []
    if page_hint is not None and 1 <= page_hint <= len(doc):
        pages_order.append(page_hint - 1)
    pages_order.extend(i for i in range(len(doc)) if i != (page_hint - 1 if page_hint else -1))

    _other_fig_pat = re.compile(r"^\s*Figure\s+\d+[:.|]")

    for page_idx in pages_order:
        page = doc[page_idx]
        blocks = page.get_text("blocks")  # [(x0,y0,x1,y1,text,block_no,type), ...]
        # text blocks only
        text_blocks = [b for b in blocks if b[6] == 0]
        text_blocks.sort(key=lambda b: (b[1], b[0]))

        cap_idx = None
        for i, b in enumerate(text_blocks):
            txt = b[4].strip()
            if txt.startswith(target):
                cap_idx = i
                break
        if cap_idx is None:
            continue

        # Other figures on the same page — use their captions as hard
        # boundaries. A figure above means fig_top can't go higher
        # than that figure's caption bottom; a figure below means
        # fig_bottom can't go past that figure's caption top.
        prev_fig_y_max = 0.0
        next_fig_y_min = float("inf")
        for k, b in enumerate(text_blocks):
            if k == cap_idx:
                continue
            if not _other_fig_pat.match(b[4].strip()):
                continue
            if b[3] < text_blocks[cap_idx][1]:  # above our caption
                if b[3] > prev_fig_y_max:
                    prev_fig_y_max = b[3]
            else:  # below our caption
                if b[1] < next_fig_y_min:
                    next_fig_y_min = b[1]

        cap_block = text_blocks[cap_idx]
        cap_x0, cap_y0, cap_x1, cap_y1, *_ = cap_block

        # Caption may span multiple consecutive blocks if PyMuPDF splits
        # them by line. Pull in following blocks that look like caption
        # continuation. PyMuPDF usually emits the whole caption as ONE
        # block, so continuation should be rare — be conservative:
        #   (a) very small vertical gap (≤ 8 pt)
        #   (b) horizontal span inside the caption x range
        #   (c) text contains lowercase letters (real prose), excluding
        #       Title-Case panel-title rows like "Base Vanilla K-SFT"
        #   (d) doesn't cross into another figure's region
        cap_y_end = cap_y1
        added = 0
        for j in range(cap_idx + 1, len(text_blocks)):
            nb = text_blocks[j]
            nb_x0, nb_y0, nb_x1, nb_y1, nb_text, *_ = nb
            if nb_y0 > cap_y_end + 8:
                break
            if nb_x0 < cap_x0 - 5 or nb_x1 > cap_x1 + 30:
                break
            if nb_y0 >= next_fig_y_min - 1:
                break
            nb_stripped = nb_text.strip()
            if _other_fig_pat.match(nb_stripped):
                break
            # Real caption prose has lowercase letters AND is not
            # dominated by isolated capitalized terms (axis ticks /
            # panel titles).
            if not any(c.islower() for c in nb_stripped):
                break
            cap_y_end = nb_y1
            added += 1
            if added >= max_caption_lines:
                break

        # Figure body bbox: above the caption, bounded by the previous
        # body-text / heading block — not by figure-internal labels
        # (axis ticks, legends, in-plot annotations). Three heuristics
        # combined:
        #   (a) block must overlap horizontally with the caption column
        #   (b) block must be a *paragraph or heading*, identified by:
        #       contains running prose (≥30 chars AND a sentence-ending
        #       punctuation '.', '!', '?', or '。') OR is a section
        #       heading (short text but starts with a digit-dot or is
        #       all-title-case alpha words, no digits).
        #   (c) the block must sit at least min_fig_height above the
        #       caption — figures are typically ≥80 pt tall.
        cap_width = cap_x1 - cap_x0
        min_fig_height = 80.0
        prev_y_bottom = 0.0

        _subcap_pat = re.compile(r"^\s*\(?[a-z]\)\s+[A-Z]")  # "(a) Recovery ..." / "a) Recovery ..."
        min_paragraph_width = cap_width * 0.55  # body / heading spans most of column
        min_heading_width = cap_width * 0.40

        def _looks_like_body(text: str, block_width: float) -> bool:
            t = text.strip()
            if len(t) < 5:
                return False
            # Sub-figure captions like "(a) Recovery ceiling. ..." are
            # PART of the figure, not the body boundary. Reject them
            # even though they read as prose.
            if _subcap_pat.match(t):
                return False
            # Paragraph: sentence terminator + substantial length +
            # spans the column width (rules out short in-figure prose
            # fragments).
            if (
                len(t) >= 30
                and any(p in t for p in (".", "!", "?", "。", "！", "？"))
                and block_width >= min_paragraph_width
            ):
                return True
            # Section heading: short capitalized title that still
            # spans a meaningful fraction of the column — rules out
            # narrow in-figure legend titles like "Qwen2.5 Series".
            tokens = t.split()
            if (
                2 <= len(tokens) <= 12
                and sum(tok[0].isalpha() and tok[0].isupper() for tok in tokens) >= 2
                and block_width >= min_heading_width
            ):
                alpha_chars = sum(c.isalpha() for c in t)
                if alpha_chars >= len(t) * 0.5:
                    return True
            return False

        for j in range(cap_idx - 1, -1, -1):
            pb_x0, pb_y0, pb_x1, pb_y1, pb_text, *_ = text_blocks[j]
            if pb_x1 < cap_x0 - 5 or pb_x0 > cap_x1 + 5:
                continue
            if cap_y0 - pb_y1 < min_fig_height:
                continue
            pb_width = pb_x1 - pb_x0
            if not _looks_like_body(pb_text, pb_width):
                continue
            prev_y_bottom = pb_y1
            break

        fig_top = max(prev_y_bottom, prev_fig_y_max) + margin_pt
        fig_bottom = cap_y_end + margin_pt if include_caption else cap_y0 - margin_pt
        if next_fig_y_min != float("inf"):
            fig_bottom = min(fig_bottom, next_fig_y_min - margin_pt)
        fig_left = cap_x0 - margin_pt
        fig_right = cap_x1 + margin_pt

        bbox = fitz.Rect(fig_left, fig_top, fig_right, fig_bottom)
        zoom = dpi / 72.0
        pix = page.get_pixmap(clip=bbox, matrix=fitz.Matrix(zoom, zoom))
        pix.save(out_path)
        doc.close()
        return (page_idx + 1, tuple(bbox))

    doc.close()
    raise ValueError(f"caption {caption_prefix!r} not found in {pdf_path}")
