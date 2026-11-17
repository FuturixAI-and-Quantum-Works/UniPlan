"""Read-only lookup in UniCAD's equipment catalog, so the plan can say which equipment the build will have to add with catalog_add.

The catalog is found at UNICAD_CATALOG, else in a UniCAD installed next to UniPlan (the shared runtime OpenCAD ships), else nowhere
(the lookup then reports 'unknown' and plan_check only warns).
"""

import json
import os
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

KIND_FILES = {
    "module": "modules.json",
    "inverter": "inverters.json",
    "pcs": "pcs.json",
    "battery": "batteries.json",
    "transformer": "transformers.json",
    "cable": "cables.json",
    "protection": "protection.json",
    "meter": "meters.json",
    "logger": "loggers.json",
}


def catalog_dir() -> Path | None:
    env = os.environ.get("UNICAD_CATALOG")
    if env and Path(env).expanduser().is_dir():
        return Path(env).expanduser().resolve()
    try:
        from unicad.mcp.config import BUNDLED_DATA  # type: ignore[import-not-found]

        p = BUNDLED_DATA / "catalog"
        return p if p.is_dir() else None
    except Exception:
        return None


def norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", text.lower())


@lru_cache(maxsize=4)
def _load(path: str, mtime: float) -> list[dict[str, Any]]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return data if isinstance(data, list) else list(data.values()) if isinstance(data, dict) else []


def entries(kind: str) -> list[dict[str, Any]] | None:
    d = catalog_dir()
    if d is None or kind not in KIND_FILES:
        return None
    f = d / KIND_FILES[kind]
    if not f.is_file():
        return []
    return _load(str(f), f.stat().st_mtime)


def summary(e: dict[str, Any]) -> dict[str, Any]:
    keep = ("id", "manufacturer", "model", "pmax", "ac_rated_kva", "rated_kva", "topology", "ac_voltage", "mppt_min", "mppt_max", "hv_kv", "lv_kv", "voc", "vmp")
    return {k: e[k] for k in keep if k in e}


def search(kind: str, query: str, limit: int = 10) -> dict[str, Any]:
    rows = entries(kind)
    if rows is None:
        return {"catalog": None, "matches": [], "note": "UniCAD's catalog was not found (set UNICAD_CATALOG); the build checks equipment itself"}
    words = [norm(w) for w in query.split() if norm(w)]
    scored = []
    for e in rows:
        hay = norm(" ".join(str(e.get(k, "")) for k in ("id", "manufacturer", "model")))
        hits = sum(1 for w in words if w in hay)
        if hits:
            scored.append((hits, e))
    scored.sort(key=lambda t: -t[0])
    best = [summary(e) for h, e in scored if h == len(words)][:limit]
    near = [summary(e) for h, e in scored if h < len(words)][: max(0, limit - len(best))]
    return {"catalog": str(catalog_dir()), "matches": best, "partial_matches": near}


def find(kind: str, manufacturer: str, model: str) -> str | None | bool:
    """The catalog id of this exact model, False when the catalog lacks it, None when there is no catalog to ask."""
    rows = entries(kind)
    if rows is None:
        return None
    m, man = norm(model), norm(manufacturer)
    for e in rows:
        if norm(str(e.get("model", ""))) == m or norm(str(e.get("id", ""))).endswith(m):
            if not man or man[:4] in norm(str(e.get("manufacturer", ""))) + norm(str(e.get("id", ""))):
                return str(e.get("id"))
    for e in rows:
        if m and m in norm(str(e.get("model", ""))):
            return str(e.get("id"))
    return False


def example_entry(kind: str) -> dict[str, Any] | None:
    """One existing entry of this kind: the schema a new entry for catalog_add must follow."""
    rows = entries(kind)
    return rows[0] if rows else None
