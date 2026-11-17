"""Source documents: copy into tender/, text per page, search, and page images the planner reads (with a pixel grid for tracing)."""

import io
import re
import shutil
from pathlib import Path

import pymupdf
from PIL import Image as PILImage, ImageDraw, ImageFont

from uniplan.store import PlanStore, Source, write_text

PDF = {".pdf"}
IMAGES = {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".webp", ".bmp"}
TEXT = {".txt", ".md", ".csv"}
SCANNED_CHARS = 80  # a page with less extractable text than this is treated as a scan
PAGE_DPI = 150
MAX_EDGE = 1600  # longest edge of an image returned to the model
MARK = "=== page {n} ==="
PAGE_RE = re.compile(r"^=== page (\d+) ===$", re.M)


def slug(text: str) -> str:
    s = re.sub(r"[^A-Za-z0-9]+", "-", text).strip("-")
    return s[:60] or "doc"


def _ocr_page(page: pymupdf.Page) -> str | None:
    """OCR one page with Tesseract through PyMuPDF; None when Tesseract or its language data is not installed."""
    try:
        tp = page.get_textpage_ocr(dpi=200, full=True)
        return page.get_text(textpage=tp)
    except Exception:
        return None


def add_source(store: PlanStore, file_path: str, title: str | None, ocr: bool) -> tuple[Source, list[str]]:
    src = Path(file_path).expanduser()
    if not src.is_file():
        raise ValueError(f"no file at {file_path}")
    ext = src.suffix.lower()
    if ext not in PDF | IMAGES | TEXT:
        raise ValueError(f"{src.name}: UniPlan reads PDF, images ({', '.join(sorted(IMAGES))}) and text ({', '.join(sorted(TEXT))}); export Word or Excel files to PDF first")
    plan = store.load()
    doc_id = slug(src.stem)
    if any(s.id == doc_id for s in plan.sources):
        raise ValueError(f"a source with id {doc_id!r} is already in the plan (source_list shows it)")
    tender = store.root / "tender"
    tender.mkdir(parents=True, exist_ok=True)
    dest = tender / src.name
    if src.resolve() != dest.resolve():
        shutil.copy2(src, dest)
    notes: list[str] = []
    if ext in IMAGES:
        source = Source(id=doc_id, file=store.rel(dest), kind="image", title=title, pages=1, scanned_pages=[1])
        notes.append("an image has no text: read it with source_page_image")
    elif ext in TEXT:
        text = dest.read_text(encoding="utf-8", errors="replace")
        txt = tender / f"{doc_id}.txt"
        if txt != dest:
            write_text(txt, MARK.format(n=1) + "\n" + text)
        else:
            notes.append("the text file is kept as given")
        source = Source(id=doc_id, file=store.rel(dest), kind="text", title=title, pages=1, text_file=store.rel(txt))
    else:
        with pymupdf.open(dest) as doc:
            chunks, scanned, landscape, ocr_used = [], [], [], False
            for i, page in enumerate(doc, start=1):
                text = page.get_text()
                if len(text.strip()) < SCANNED_CHARS:
                    scanned.append(i)
                    if ocr:
                        got = _ocr_page(page)
                        if got is not None:
                            text, ocr_used = got, True
                r = page.rect
                if r.width > r.height:
                    landscape.append(i)
                chunks.append(MARK.format(n=i) + "\n" + text.strip() + "\n")
            pages = doc.page_count
        txt = tender / f"{doc_id}.txt"
        write_text(txt, "\n".join(chunks))
        source = Source(id=doc_id, file=store.rel(dest), kind="pdf", title=title, pages=pages, text_file=store.rel(txt),
                        scanned_pages=scanned, landscape_pages=landscape, ocr=ocr_used)  # fmt: skip
        if scanned and not ocr_used:
            notes.append(f"{len(scanned)} of {pages} pages are scans with no text layer and OCR was not available: read them as images with source_page_image")
        elif scanned:
            notes.append(f"{len(scanned)} scanned pages were OCR'd: the OCR is imperfect, so check every number that matters against source_page_image")
        if landscape:
            notes.append(f"landscape pages {landscape}: site plans and tables often sit there")
    plan.sources.append(source)
    store.save(plan)
    return source, notes


def get_source(store: PlanStore, doc: str) -> Source:
    plan = store.load()
    for s in plan.sources:
        if s.id == doc:
            return s
    raise ValueError(f"no source {doc!r}; sources: {', '.join(s.id for s in plan.sources) or 'none (source_add first)'}")


def page_texts(store: PlanStore, s: Source) -> dict[int, str]:
    if not s.text_file:
        return {}
    text = store.path(s.text_file).read_text(encoding="utf-8", errors="replace")
    parts = PAGE_RE.split(text)
    return {int(parts[i]): parts[i + 1].strip() for i in range(1, len(parts) - 1, 2)}


def parse_pages(spec: str | None, last: int) -> list[int]:
    if not spec:
        return list(range(1, last + 1))
    out: list[int] = []
    for part in spec.split(","):
        part = part.strip()
        if "-" in part:
            a, b = part.split("-", 1)
            out.extend(range(int(a), int(b) + 1))
        elif part:
            out.append(int(part))
    bad = [p for p in out if p < 1 or p > last]
    if bad:
        raise ValueError(f"pages {bad} are outside 1-{last}")
    return out


def read_pages(store: PlanStore, doc: str, pages: str | None, max_chars: int) -> dict:
    s = get_source(store, doc)
    texts = page_texts(store, s)
    if not texts:
        raise ValueError(f"{doc} has no text: read it with source_page_image")
    want = parse_pages(pages, s.pages)
    out, used, cut, shown = [], 0, None, []
    for p in want:
        t = texts.get(p, "")
        block = f"--- {doc} p.{p}{' (scan, no text layer)' if p in s.scanned_pages and not s.ocr else ''} ---\n{t}"
        if used + len(block) > max_chars and out:
            cut = p
            break
        out.append(block)
        used += len(block)
        shown.append(p)
    plan = store.load()
    for src in plan.sources:
        if src.id == s.id:
            src.read_pages = sorted(set(src.read_pages) | set(shown))
    store.save(plan)
    return {"doc": doc, "text": "\n\n".join(out), **({"truncated_at_page": cut, "hint": f"continue with pages='{cut}-{want[-1]}'"} if cut else {})}


def search(store: PlanStore, query: str, doc: str | None, regex: bool, context: int, limit: int) -> list[dict]:
    plan = store.load()
    docs = [get_source(store, doc)] if doc else plan.sources
    pat = re.compile(query if regex else re.escape(query), re.I)
    hits: list[dict] = []
    for s in docs:
        for p, t in page_texts(store, s).items():
            for m in pat.finditer(t):
                a, b = max(0, m.start() - context), min(len(t), m.end() + context)
                hits.append({"doc": s.id, "page": p, "match": m.group(0), "snippet": re.sub(r"\s+", " ", t[a:b]).strip()})
                if len(hits) >= limit:
                    return hits
    return hits


def page_image_path(store: PlanStore, s: Source, page: int, rotate: int) -> Path:
    """The page rendered at PAGE_DPI and rotated, saved once under tender/pages. Its pixels are the frame site_trace reads."""
    if s.kind == "image":
        if page != 1:
            raise ValueError(f"{s.id} is an image: page is 1")
        base = store.path(s.file)
        if rotate % 360 == 0:
            return base
        out = store.root / "tender" / "pages" / f"{s.id}-r{rotate % 360}.png"
        if not out.exists():
            with PILImage.open(base) as im:
                im.rotate(-rotate, expand=True).save(out)
        return out
    if s.kind != "pdf":
        raise ValueError(f"{s.id} is text: use source_read")
    if not 1 <= page <= s.pages:
        raise ValueError(f"page {page} is outside 1-{s.pages}")
    suffix = f"-r{rotate % 360}" if rotate % 360 else ""
    out = store.root / "tender" / "pages" / f"{s.id}-p{page:03d}{suffix}.png"
    if not out.exists():
        out.parent.mkdir(parents=True, exist_ok=True)
        with pymupdf.open(store.path(s.file)) as doc:
            pix = doc[page - 1].get_pixmap(dpi=PAGE_DPI)
            im = PILImage.open(io.BytesIO(pix.tobytes("png")))
            if rotate % 360:
                im = im.rotate(-rotate, expand=True)
            im.save(out)
    return out


def _font(size: int):
    try:
        return ImageFont.load_default(size=size)
    except TypeError:
        return ImageFont.load_default()


def render_view(path: Path, crop: list[float] | None, grid: float | None, max_edge: int = MAX_EDGE) -> tuple[bytes, dict]:
    """A PNG of `path` (optionally cropped to [x0, y0, x1, y1] in its pixels), scaled to fit max_edge, with an optional labelled grid
    every `grid` pixels of the full image so points read off the view are in full-image pixels."""
    with PILImage.open(path) as im:
        im = im.convert("RGB")
        W, H = im.size
        x0, y0, x1, y1 = (0, 0, W, H) if not crop else (max(0, crop[0]), max(0, crop[1]), min(W, crop[2]), min(H, crop[3]))
        if x1 <= x0 or y1 <= y0:
            raise ValueError(f"crop {crop} is empty inside the {W} x {H} image")
        view = im.crop((int(x0), int(y0), int(x1), int(y1)))
        k = min(1.0 * 4, max_edge / max(view.size))  # up to 4x zoom on small crops
        view = view.resize((max(1, round(view.width * k)), max(1, round(view.height * k))), PILImage.LANCZOS)
        if grid:
            d = ImageDraw.Draw(view)
            f = _font(14)
            gx = (int(x0 // grid) + 1) * grid
            while gx < x1:
                X = (gx - x0) * k
                d.line([(X, 0), (X, view.height)], fill=(255, 0, 0), width=1)
                d.text((X + 2, 2), f"{gx:g}", fill=(255, 0, 0), font=f)
                gx += grid
            gy = (int(y0 // grid) + 1) * grid
            while gy < y1:
                Y = (gy - y0) * k
                d.line([(0, Y), (view.width, Y)], fill=(0, 0, 255), width=1)
                d.text((2, Y + 2), f"{gy:g}", fill=(0, 0, 255), font=f)
                gy += grid
        buf = io.BytesIO()
        view.save(buf, "PNG", optimize=True)
    return buf.getvalue(), {"image_px": [W, H], "crop": [x0, y0, x1, y1], "zoom": round(k, 3)}
