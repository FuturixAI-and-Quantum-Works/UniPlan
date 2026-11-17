"""engineering-brief.md and AGENT_PROMPT.md, rendered from plan.json. The prompt is written for a build model that has only UniCAD's tools
and OpenCAD's file tools, starts with an empty context and has never seen the tender: everything it needs is in the plan folder."""

import re
from typing import Any

from uniplan.schema import FIELD_SPECS, SECTIONS, applies
from uniplan.store import FieldValue, Plan


def fmt_value(v: Any) -> str:
    if isinstance(v, list):
        return "; ".join(fmt_value(x) for x in v)
    if isinstance(v, dict):
        return "; ".join(f"{k}: {fmt_value(x)}" for k, x in v.items())
    if isinstance(v, float):
        return f"{v:g}"
    return str(v)


def fmt_field(fv: FieldValue, with_source: bool = True) -> str:
    text = fmt_value(fv.value) + (f" {fv.unit}" if fv.unit and not fmt_value(fv.value).endswith(fv.unit) else "")
    if fv.status == "assumed":
        text = f"**ASSUMED:** {text} ({fv.note})"
    elif fv.status == "inferred":
        text = f"{text} **[inferred: {fv.note}]**"
    elif fv.status == "not_applicable":
        text = f"not applicable ({fv.note})"
    elif fv.note:
        text = f"{text} ({fv.note})"
    if with_source and fv.source:
        text += f" [{fv.source}]"
    return text


def cell(text: str) -> str:
    return text.replace("|", "/").replace("\n", " ")


def label(key: str) -> str:
    spec = FIELD_SPECS.get(key)
    return spec.label if spec else key.split(".", 1)[1].replace("_", " ").capitalize()


def section_rows(plan: Plan, section: str) -> list[tuple[str, FieldValue]]:
    order = [f"{section}.{f.key}" for s in SECTIONS if s.key == section for f in s.fields]
    known = [(k, plan.fields[k]) for k in order if k in plan.fields]
    extra = sorted((k, v) for k, v in plan.fields.items() if k.startswith(section + ".") and k not in order)
    return known + extra


def g(plan: Plan, key: str, default: str = "") -> str:
    fv = plan.fields.get(key)
    return fmt_value(fv.value) if fv else default


def brief(plan: Plan) -> str:
    out = [f"# Engineering brief: {plan.name}", ""]
    out.append("Sources, in `tender/` of this folder (text per page in `<doc>.txt`, page images in `tender/pages/`):")
    for s in plan.sources:
        extra = []
        if s.kind == "pdf":
            extra.append(f"{s.pages} pp")
            if s.scanned_pages:
                extra.append(f"scanned pages {_ranges(s.scanned_pages)}" + (" (OCR'd, imperfect)" if s.ocr else " (no text layer: read as images)"))
            if s.landscape_pages:
                extra.append(f"landscape pages {_ranges(s.landscape_pages)}")
        out.append(f"- `{s.file}` ({s.id}{': ' + s.title if s.title else ''}{'; ' + ', '.join(extra) if extra else ''})")
    out += ["", "Values are quoted from the documents with their source in [brackets]. **[inferred]** values are derived; **ASSUMED** values are choices the documents do not make. When a number matters, check it against the page image.", ""]

    n = 1
    for sec in SECTIONS:
        rows = section_rows(plan, sec.key)
        if not rows:
            continue
        out += [f"## {n}. {sec.title}", "", "| Item | Value |", "|---|---|"]
        out += [f"| {cell(label(k))} | {cell(fmt_field(v))} |" for k, v in rows]
        out.append("")
        n += 1

    if plan.equipment:
        out += [f"## {n}. Equipment the build starts from", "", "| Role | Make / model | UniCAD catalog | Source |", "|---|---|---|---|"]
        for e in plan.equipment:
            cat = f"`{e.catalog_id}`" if e.catalog_id else ("missing: add from `catalog/needs.json`" if e.entry else f"missing: datasheet {e.datasheet}" if e.datasheet else "missing, no datasheet")
            out.append(f"| {e.role} | {cell(e.manufacturer)} {cell(e.model)} | {cell(cat)} | {cell(e.source or '')} |")
        out.append("")
        n += 1

    if plan.site:
        st = plan.site
        out += [f"## {n}. Site (digitised, every feature ASSUMED)", ""]
        out.append(f"`{st.geojson}` traced from `{st.image}`; scale {st.m_per_px:g} m/px from {st.scale_from}; anchored at {st.anchor_lat}, {st.anchor_lon}, north up as drawn.")
        out += ["", "| Feature | Kind | Area / length | Stated |", "|---|---|---|---|"]
        for f in st.features:
            size = f"{f.area_m2:.0f} m2" if f.area_m2 is not None else f"{f.length_m:.0f} m" if f.length_m is not None else ""
            out.append(f"| {cell(f.name)} | {f.kind} | {size} | {f'{f.stated_area_m2:.0f} m2' if f.stated_area_m2 else ''} |")
        out += [""] + [f"- {x}" for x in st.notes] + ([""] if st.notes else [])
        n += 1

    conflicts = [i for i in plan.issues if i.kind == "conflict"]
    gaps = [i for i in plan.issues if i.kind == "gap"]
    opens = [i for i in plan.issues if i.kind == "open_point"]
    if conflicts:
        out += [f"## {n}. Documents that conflict (choice made)", ""]
        for i in conflicts:
            opts = "; ".join(f"{o.value}" + (f" [{o.source}]" if o.source else "") for o in i.options)
            out.append(f"- **{i.topic}**: {opts}. **Decision:** {i.decision} ({i.reason})")
        out.append("")
        n += 1
    if gaps:
        out += [f"## {n}. Gaps the documents leave open (decide, mark ASSUMED, report)", ""]
        for i in gaps:
            d = f" Decision: **{i.decision}** ({i.reason})" if i.decision else " Decide in the build and mark it ASSUMED."
            out.append(f"- **{i.topic}**: {i.detail}{d}")
        out.append("")
        n += 1
    if opens:
        out += [f"## {n}. Open points for the client", ""] + [f"- **{i.topic}**: {i.detail}" for i in opens] + [""]
        n += 1
    if plan.notes:
        out += [f"## {n}. Notes", ""] + [f"- {x}" for x in plan.notes] + [""]
    return "\n".join(out).rstrip() + "\n"


def _ranges(pages: list[int]) -> str:
    out, start, prev = [], None, None
    for p in sorted(pages):
        if start is None:
            start = prev = p
        elif p == prev + 1:
            prev = p
        else:
            out.append(f"{start}-{prev}" if start != prev else f"{start}")
            start = prev = p
    if start is not None:
        out.append(f"{start}-{prev}" if start != prev else f"{start}")
    return ", ".join(out)


def suggest_template(plan: Plan) -> str:
    if plan.segment == "rooftop":
        return "ci_rooftop"
    if plan.segment == "bess":
        return "bess_ac"
    if plan.segment == "hybrid":
        return "hybrid"
    v = None
    fv = plan.fields.get("grid.export_voltage_v")
    if fv is not None:
        m = re.search(r"[\d.]+", fmt_value(fv.value))
        if m:
            v = float(m.group(0))
            if "kv" in (fmt_value(fv.value) + (fv.unit or "")).lower():
                v *= 1000
    if v is not None and v <= 1000:
        return "utility_lt"
    inv = (g(plan, "conversion.inverter") + " " + " ".join(e.model for e in plan.equipment if e.role == "inverter")).lower()
    return "utility_central" if "central" in inv else "utility_string"


def prompt(plan: Plan, work: str, template: str | None, extra_rules: list[str]) -> str:
    template = template or suggest_template(plan)
    client, epc = g(plan, "project.client", "the client"), g(plan, "project.epc", "the EPC contractor")
    loc = g(plan, "project.location")
    cap = " / ".join(x for x in (g(plan, "capacity.dc_kwp") and f"{g(plan, 'capacity.dc_kwp')} kWp DC", g(plan, "capacity.ac_kw") and f"{g(plan, 'capacity.ac_kw')} kW AC") if x)
    stage = g(plan, "project.stage", "approval-stage")
    o: list[str] = []
    o += [f"# Task: {stage} drawing set for {plan.name}, built with the UniCAD MCP", ""]
    o += [f"You are the design engineer for {epc} on {client}'s {plan.name}{' (' + cap + ')' if cap else ''}{', ' + loc if loc else ''}. "
          "Produce the complete electrical drawing set and schedules the contract asks for, built only through the UniCAD MCP server. "
          "This prompt and the files next to it are your whole brief: a planner has already read the tender, resolved its conflicts and recorded "
          "every value with its source. Do not re-plan; build, check and report.", ""]  # fmt: skip

    o += ["## Inputs (the only inputs you may use)", "", f"Folder: `{work}/`"]
    o.append("- `engineering-brief.md`: every contract value with its source, the conflicts already decided, the gaps and open points. Read it first, completely.")
    for s in plan.sources:
        o.append(f"- `{s.file}`" + (f" (text per page: `{s.text_file}`)" if s.text_file else "") + (f": {s.title}" if s.title else "") + ". Go back to it only to check a number the brief quotes.")
    if plan.site:
        o.append(f"- `{plan.site.geojson}`: the digitised site, ready for site_import (every feature ASSUMED; see the brief's Site section).")
    needs = [e for e in plan.equipment if not e.catalog_id]
    if needs:
        o.append("- `catalog/needs.json`: equipment UniCAD's catalog lacks, with drafted entries from the manufacturers' datasheets.")
    o.append("")

    o += ["## Tooling", ""]
    o.append("- UniCAD is connected as an MCP server: call its tools directly. Its server instructions give the workflow; its new_utility_project / new_rooftop_project / new_bess_project prompts give step plans. Do not edit UniCAD's source, catalog files or tests.")
    o.append("- Do every computation (string voltages, ratios, losses, areas) with the calculate tool, never by hand. Read files with read_file and write them with write_file.")
    o.append(f"- Keep a log of every UniCAD call you make, in order, with its arguments and the key result, in `{work}/model/steps.md`, so the set can be regenerated.")
    if needs:
        o.append("- Add each entry of `catalog/needs.json` with catalog_add(entry) before you use it (never write catalog JSON files). If catalog_add rejects an entry, fix the field it names from the datasheet URL recorded in the entry; do not invent values.")
    o.append("")

    o += ["## Design intent from the contract", ""]
    for sec in SECTIONS:
        if sec.key in ("project", "deliverables", "reference_design"):
            continue
        for k, v in section_rows(plan, sec.key):
            if v.status == "not_applicable":
                continue
            o.append(f"- {label(k)}: {fmt_field(v, with_source=False)}.")
    o.append("")
    o += ["## Equipment", ""]
    for e in plan.equipment:
        where = f"catalog id `{e.catalog_id}`" if e.catalog_id else "catalog_add it from `catalog/needs.json`" if e.entry else f"not in the catalog: find its datasheet ({e.datasheet or 'no URL recorded'}) and catalog_add it"
        o.append(f"- {e.role}: {e.manufacturer} {e.model} ({where}){'. ' + e.note if e.note else ''}.")
    o.append("")

    ref = [(k, v) for k, v in section_rows(plan, "reference_design")]
    if ref:
        o += ["## Reference design and the checks to make on it", ""]
        o += [f"- {label(k)}: {fmt_field(v, with_source=False)}" for k, v in ref]
        o.append("- If the reference design does not fit or fails a check, place the maximum that complies, report the shortfall with numbers, and do not move the plant onto excluded areas or change contract values to make it fit.")
        o.append("")

    decided = [i for i in plan.issues if i.kind == "conflict"] + [i for i in plan.issues if i.kind == "gap" and i.decision]
    open_gaps = [i for i in plan.issues if i.kind == "gap" and not i.decision]
    if decided or open_gaps:
        o += ["## Decisions already made (follow them; list them in the README)", ""]
        o += [f"- {i.topic}: **{i.decision}** ({i.reason})." for i in decided]
        if open_gaps:
            o += ["", "Left to you (decide, mark ASSUMED with the reason, report):"]
            o += [f"- {i.topic}: {i.detail}" for i in open_gaps]
        o.append("")

    if plan.site:
        o += ["## Site", ""]
        o.append(f"Import `{work}/{plan.site.geojson}` with site_import. It was traced from `{plan.site.image}` (scale from {plan.site.scale_from}); every feature carries ASSUMED in its name and must keep it. Apply the setbacks the brief gives with site_set_setbacks and check site_summary against the stated areas.")
        o += [f"- {x}" for x in plan.site.notes]
        o.append("")

    o += ["## Build stages", "", "Work stage by stage. Finish each stage's check before the next; if a check fails twice for the same reason, see Escalation.", ""]
    stages = [
        ("Read", "engineering-brief.md completely, then this prompt again.", "you can state the module, inverter, export voltage, POI and the decided conflicts without looking."),
        ("Company and project", f"company_list; company_assign if {epc} has a profile, otherwise stay on 'default' and record it as a tool gap. project_create (segment '{plan.segment}', grid voltage as the brief states it), project_design_basis with every value the brief gives and assumed=[...] for every ASSUMED one.", "project_get lists your ASSUMED inputs under assumed_inputs and no placeholder you could have filled from the brief remains."),
        ("Catalog", "catalog_search / catalog_get the equipment above; catalog_add what is missing.", "catalog_get returns every equipment id you will use."),
    ]
    if plan.segment != "bess":
        stages.append(("Site", f"site_import the site file; site_set_setbacks; site_summary.", "the buildable area is reported and every traced feature still says ASSUMED."))
        stages.append(("Strings and layout", "design_strings for the module and inverter at the brief's ambient limits; layout_place with the table format, tilt, pitch and gaps above; layout_summary.", "DC kWp and module count are reported against the target; any shortfall is computed with calculate and noted."))
    stages += [
        ("Network", f"network_autobuild (template '{template}', suggested by the planner from the export voltage and inverter type), then validate.", "validate reports no errors. Fix errors by changing the design (explain_finding first), never by editing a value to silence a rule."),
        ("Earthing and lightning", "earthing_design and lps_design with the brief's inputs; validate again.", "both designs exist and validate still has no errors; differences from the contract's pit counts or strip sizes are listed as tool gaps."),
        ("Schedules", "cable_schedule and bom as xlsx.", "both files exist and the cable losses meet the brief's DC and AC limits."),
        ("Issue", f"project_snapshot, then project_issue ({g(plan, 'deliverables.issue', 'issued_for approval, rev 0')}), then drawings_render(kinds=['all']).", "the set renders with the revision, names and date on every sheet."),
        ("Report", "write the README described under Deliverables.", "every drawing on the contract's list is mapped to a sheet or marked not produced with the reason."),
    ]
    for i, (name, do, done) in enumerate(stages, start=1):
        o.append(f"{i}. **{name}.** {do} Done when {done}")
    o.append("")

    o += ["## Rules of work", ""]
    rules = [
        "Every value you choose that is not in the brief goes in UniCAD's assumed lists or carries 'ASSUMED: <what and why>', exactly as the UniCAD server instructions say.",
        "Never change a contract value (capacity, voltages, ratings, limits) to get around a tool error or a missing catalog item.",
        "If a UniCAD tool cannot represent something the contract requires, do not work around it by hacking files: record a tool gap with the exact tool, input and error, and carry on with what the tool can do.",
        "Do not re-read the whole tender. The brief is the source; open a tender page only to check a value the brief quotes.",
    ] + extra_rules
    o += [f"- {r}" for r in rules]
    o.append("")

    o += ["## Escalation", "",
          f"Stop and escalate (write `{work}/ESCALATION.md` with the question, the evidence and the options you see, and end your turn with a message starting 'ESCALATE:') when:",
          "- the contract cannot be met as briefed (capacity or ratio outside its band, equipment that cannot work together) and the brief does not say what to do;",
          "- a fix would need a contract value changed or a decision the brief did not make;",
          "- the same check fails after two genuine design changes.",
          "Do not escalate tool errors you can fix by reading the error and the UniCAD instructions.", ""]  # fmt: skip

    o += ["## Deliverables", "", f"In `{work}/`:"]
    o.append("- `drawings/`: the rendered set (DXF, PDF, DWG and the combined set.pdf).")
    o.append("- `schedules/`: cable schedule and BOM as xlsx.")
    o.append("- `model/steps.md`: the call log.")
    o.append("- `README.md`: design summary (module, count, strings, combiners, inverter, transformer, switchgear, cables, earthing result, LPS result, validate result), assumed inputs with basis, decided conflicts, open points, catalog additions, tool gaps, and a compliance table mapping each drawing on the contract's list to the sheet that covers it or 'not produced (reason)'.")
    dl = plan.fields.get("deliverables.drawing_list")
    if dl:
        o += ["", "The contract's drawing list:", "", *(f"- {x}" for x in (dl.value if isinstance(dl.value, list) else [fmt_value(dl.value)]))]
    o += ["", "Finish with a short report: what was built, DC kWp and module count achieved vs the target, validate errors and warnings, assumptions, tool gaps, and the path to `drawings/set.pdf`.", ""]
    return "\n".join(o)
