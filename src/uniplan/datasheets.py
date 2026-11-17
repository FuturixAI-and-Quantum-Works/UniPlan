"""Fetch a manufacturer datasheet into catalog/datasheets/, so an equipment entry can only cite a document that was really retrieved
and that names the model (plan_check compares them)."""

import datetime as dt
import hashlib
import re
import urllib.error
import urllib.request

import pymupdf

from uniplan.store import Datasheet, PlanStore, write_text

MAX_BYTES = 30 * 1024 * 1024
UA = "Mozilla/5.0 (X11; Linux x86_64) UniPlan datasheet fetch"


def html_text(raw: bytes) -> str:
    text = raw.decode("utf-8", errors="replace")
    text = re.sub(r"<(script|style)\b.*?</\1>", " ", text, flags=re.S | re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def fetch(store: PlanStore, url: str, timeout: float = 60) -> tuple[Datasheet, str]:
    if not re.match(r"https?://", url):
        raise ValueError("datasheet_fetch takes an http(s) URL")
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/pdf,text/html;q=0.9,*/*;q=0.5"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read(MAX_BYTES + 1)
            ctype = r.headers.get("content-type", "")
            final = r.geturl()
    except urllib.error.HTTPError as e:
        raise ValueError(f"{url}: HTTP {e.code}; the URL does not serve a document") from None
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise ValueError(f"{url}: cannot fetch ({getattr(e, 'reason', e)})") from None
    if len(raw) > MAX_BYTES:
        raise ValueError(f"{url}: larger than {MAX_BYTES // 1048576} MB")
    is_pdf = raw[:5] == b"%PDF-" or "pdf" in ctype
    name = hashlib.sha1(url.encode()).hexdigest()[:12]
    folder = store.root / "catalog" / "datasheets"
    folder.mkdir(parents=True, exist_ok=True)
    if is_pdf:
        path = folder / f"{name}.pdf"
        path.write_bytes(raw)
        with pymupdf.open(path) as d:
            text = "\n".join(f"=== page {i + 1} ===\n{p.get_text()}" for i, p in enumerate(d))
    else:
        path = folder / f"{name}.html"
        path.write_bytes(raw)
        text = html_text(raw)
    if len(text.strip()) < 200:
        raise ValueError(f"{url}: the document has almost no text ({len(text.strip())} characters); find a text datasheet (PDF or product page)")
    txt = folder / f"{name}.txt"
    write_text(txt, f"URL: {url}\nFINAL URL: {final}\n\n{text}")
    ds = Datasheet(url=url, file=store.rel(path), text_file=store.rel(txt), fetched=dt.datetime.now().isoformat(timespec="seconds"), bytes=len(raw))
    plan = store.load()
    plan.datasheets = [d for d in plan.datasheets if d.url != url] + [ds]
    store.save(plan)
    return ds, text


def reader(store: PlanStore):
    def text_of(ds: Datasheet) -> str:
        try:
            return store.path(ds.text_file).read_text(encoding="utf-8", errors="replace")
        except OSError:
            return ""

    return text_of
