"""plan_check: what keeps a plan from being handed to a build model. Errors block plan_write; warnings are reported."""

import re
from typing import Any

from uniplan import catalog
from uniplan.schema import FIELD_SPECS, applies
from uniplan.store import Plan

AREA_TOLERANCE = 0.02


def _ranges(pages: list[int]) -> str:
    from uniplan.render import _ranges as r

    return r(pages)


def _num(v: Any) -> float | None:
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        m = re.match(r"\s*([-+]?\d[\d,]*\.?\d*)", v)
        if m:
            try:
                return float(m.group(1).replace(",", ""))
            except ValueError:
                return None
    return None


def sanity(kind: str, e: dict) -> list[str]:
    """Physical plausibility of a drafted entry: catches values misread from OCR or another table."""
    out = []
    n = {k: _num(v) for k, v in e.items()}
    if kind == "module":
        voc, vmp, isc, imp, p = (n.get(k) for k in ("voc", "vmp", "isc", "imp", "pmax"))
        if voc and vmp and vmp >= voc:
            out.append(f"vmp {vmp:g} V is not below voc {voc:g} V")
        if isc and imp and imp >= isc:
            out.append(f"imp {imp:g} A is not below isc {isc:g} A")
        if p and vmp and imp and abs(vmp * imp / p - 1) > 0.05:
            out.append(f"vmp x imp = {vmp * imp:.0f} W, not pmax {p:g} W")
        if voc and not 20 <= voc <= 80:
            out.append(f"voc {voc:g} V is not a single module's")
    if kind == "inverter":
        lo, hi, vmax = (n.get(k) for k in ("mppt_min", "mppt_max", "dc_max_voltage"))
        if lo and hi and lo >= hi:
            out.append(f"mppt_min {lo:g} V is not below mppt_max {hi:g} V")
        if hi and vmax and hi > vmax:
            out.append(f"mppt_max {hi:g} V is above dc_max_voltage {vmax:g} V")
    return out


def check(plan: Plan, datasheet_text=None) -> dict[str, Any]:
    errors: list[str] = []
    warnings: list[str] = []
    seg = plan.segment

    if not plan.sources:
        errors.append("no source documents: source_add the tender / PRD first")

    for key, spec in FIELD_SPECS.items():
        if not applies(spec, seg):
            continue
        fv = plan.fields.get(key)
        if fv is None:
            (errors if spec.level == "must" else warnings).append(f"{key} ({spec.label}) is not set; look in: {spec.where}")
            continue
    for key, fv in plan.fields.items():
        if fv.status == "stated" and not fv.source:
            errors.append(f"{key}: a stated value needs its source (document, page, clause)")
        elif fv.status == "inferred" and not (fv.source and fv.note):
            errors.append(f"{key}: an inferred value needs the source it is inferred from and a note saying how")
        elif fv.status == "assumed" and not fv.note:
            errors.append(f"{key}: an assumed value needs a note saying what was assumed and why")
        elif fv.status == "not_applicable" and not fv.note:
            errors.append(f"{key}: not_applicable needs a note saying why")

    for i in plan.issues:
        if i.kind == "conflict" and not (i.decision and i.reason):
            errors.append(f"issue {i.id} ({i.topic}): a conflict needs a decision and its reason")
        if i.kind == "conflict" and len(i.options) < 2:
            warnings.append(f"issue {i.id} ({i.topic}): a conflict normally lists the competing values with their sources")
        if i.kind == "gap" and not i.decision:
            warnings.append(f"issue {i.id} ({i.topic}): an open gap without a decision is passed to the builder as 'decide and mark ASSUMED'")

    # equipment
    roles = {e.role for e in plan.equipment}
    if seg in ("utility", "rooftop", "hybrid"):
        for role in ("module", "inverter"):
            if role not in roles:
                errors.append(f"equipment: no {role}; equipment_set the {role} the build starts from")
    if seg in ("bess", "hybrid"):
        for role in ("pcs", "battery"):
            if role not in roles:
                errors.append(f"equipment: no {role}; equipment_set it")
    for e in plan.equipment:
        if e.catalog_id is None and e.datasheet and not e.entry:
            warnings.append(f"equipment {e.role} {e.manufacturer} {e.model}: datasheet recorded but no catalog entry drafted; drafting the entry (equipment_set entry=...) saves the build model the datasheet reading")

    for e in plan.equipment:
        if e.catalog_id is not None:
            continue
        fetched = {d.url: d for d in plan.datasheets}
        if not (e.datasheet and e.datasheet in fetched):
            errors.append(f"equipment {e.role} {e.manufacturer} {e.model}: not in UniCAD's catalog and its datasheet was not fetched with datasheet_fetch; fetch the manufacturer's datasheet and pass that exact URL (the tender's own summary, e.g. a PVsyst report, is not a datasheet)")
        elif datasheet_text is not None and catalog.norm(e.model) not in catalog.norm(datasheet_text(fetched[e.datasheet])):
            errors.append(f"equipment {e.role} {e.model}: the fetched datasheet {e.datasheet} does not mention model {e.model!r}; it is not this product's datasheet")
        if e.entry:
            ex = catalog.example_entry(e.kind) or {}
            nulls = [k for k, v in e.entry.items() if v is None and isinstance(ex.get(k), (int, float)) and not isinstance(ex.get(k), bool)]
            if nulls:
                errors.append(f"equipment {e.role} {e.model}: entry has no value for {', '.join(nulls)}; take them from the datasheet")
            errors += [f"equipment {e.role} {e.model}: {p}" for p in sanity(e.kind, e.entry)]

    # pages read as images: OCR garbles numbers, and site plans, tables and drawings sit on scans and landscape pages
    for s in plan.sources:
        if s.kind != "pdf":
            continue
        unread = [p for p in range(1, s.pages + 1) if p not in s.read_pages and p not in s.viewed_pages]
        if unread:
            errors.append(f"{s.id}: pages {_ranges(unread)} were never read (source_read or source_page_image); every page must be read before the plan is written, a project-specific report can sit anywhere")
        unseen = [p for p in s.landscape_pages if p not in s.viewed_pages]
        if unseen:
            errors.append(f"{s.id}: landscape pages {unseen} were never viewed (source_page_image); site plans, single-line diagrams and tables sit on such pages")
        if s.scanned_pages and not s.viewed_pages:
            errors.append(f"{s.id}: {len(s.scanned_pages)} scanned pages and no page image viewed; check the numbers you recorded from OCR on the page images")

    # site
    if seg in ("utility", "hybrid"):
        sf = plan.fields.get("site.site_file")
        if plan.site is None and not (sf and sf.status == "stated"):
            errors.append("site: no site geometry. Trace the site plan with site_trace, or set site.site_file (stated, with its source) to a survey file the tender provides. A ground-mount plant cannot be laid out without a site, so not_applicable is not accepted; if the documents truly have no plan, record it as a gap and an open point and trace nothing")
    if plan.site:
        for f in plan.site.features:
            if f.stated_area_m2 and f.area_m2 and abs(f.area_m2 / f.stated_area_m2 - 1) > AREA_TOLERANCE:
                errors.append(f"site: {f.name} is {f.area_m2:.0f} m2 against {f.stated_area_m2:.0f} m2 stated (> {AREA_TOLERANCE:.0%}); normalise it or fix the trace")

    if plan.site:
        loose = [f.name for f in plan.site.features if f.kind not in ("road", "transmission_line", "setback_line") and not f.stated_area_m2]
        if loose:
            warnings.append(f"site: no stated area for {', '.join(loose)}; where the documents state an area, pass stated_area_m2 so the trace is scaled and checked against it")

    # consistency of numbers
    fv = plan.fields
    dc = _num(fv["capacity.dc_kwp"].value) if "capacity.dc_kwp" in fv else None
    ac = _num(fv["capacity.ac_kw"].value) if "capacity.ac_kw" in fv else None
    ratio = _num(fv["capacity.dc_ac_ratio"].value) if "capacity.dc_ac_ratio" in fv else None
    if dc and ac and ratio and abs(dc / ac - ratio) / ratio > 0.03:
        warnings.append(f"capacity: dc_kwp / ac_kw = {dc / ac:.2f} but dc_ac_ratio says {ratio:g}; record which one governs as a conflict")
    n = _num(fv["pv.module_count"].value) if "pv.module_count" in fv else None
    mps = _num(fv["pv.modules_per_string"].value) if "pv.modules_per_string" in fv else None
    st = _num(fv["pv.strings"].value) if "pv.strings" in fv else None
    if n and mps and st and abs(mps * st - n) > 0.5:
        warnings.append(f"pv: {mps:g} modules x {st:g} strings = {mps * st:g}, not module_count {n:g}")

    dl = fv.get("deliverables.drawing_list")
    if dl is not None and dl.status != "not_applicable" and (not isinstance(dl.value, list) or len(dl.value) < 10):
        warnings.append("deliverables.drawing_list: give the contract's list item by item (a list, as the document lists them), not a summary: the build maps every item to a sheet")
    unknown = [k for k in fv if k not in FIELD_SPECS]
    return {
        "ready": not errors,
        "errors": errors,
        "warnings": warnings,
        "counts": {
            "fields": len(fv), "extra_fields": len(unknown), "issues": len(plan.issues), "equipment": len(plan.equipment),
            "assumed": sum(1 for v in fv.values() if v.status == "assumed"), "inferred": sum(1 for v in fv.values() if v.status == "inferred"),
        },  # fmt: skip
        "catalog": str(catalog.catalog_dir()) if catalog.catalog_dir() else None,
    }
