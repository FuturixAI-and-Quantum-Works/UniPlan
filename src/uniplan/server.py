"""The UniPlan MCP server: a planner's tools for turning a tender or PRD into a plan folder a build model can work from."""

import json
from importlib import metadata
from pathlib import Path
from typing import Annotated, Any, Literal

from mcp.server.mcpserver import Image, MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import BaseModel, Field, ValidationError

from uniplan import calc, catalog, checks, datasheets, render, sources
from uniplan.schema import SECTION_KEYS, schema_for
from uniplan.site import KnownDistance, TraceFeature, preview, site_plan, trace
from uniplan.store import Equipment, FieldValue, Issue, Option, PlanStore, Value, write_json, write_text

READ_ONLY = ToolAnnotations(readOnlyHint=True)

INSTRUCTIONS = """UniPlan turns a tender, RFP or PRD into a build plan: a folder a separate build model (any model, with only UniCAD's tools) can work from with a fresh context and without ever reading the tender. You are the planner. Read carefully, decide, record every value with its source; do not design the plant (that is the build's job with UniCAD).

The plan folder gets: tender/ (the documents, text per page, page images), engineering-brief.md (every contract value with its source, conflicts decided, gaps, open points), AGENT_PROMPT.md (the build model's task: inputs, design intent, equipment, decisions, stages with done-checks, rules, escalation, deliverables), model/site.geojson (the digitised site) and catalog/needs.json (equipment UniCAD's catalog lacks, drafted from datasheets).

Workflow:
1. plan_status. If there is no plan, plan_create(name, segment).
2. source_add every document the user gave (PDF, images, text). Read its notes: scanned pages without OCR must be read as images (source_page_image).
3. Read the documents in page ranges (source_read), and record as you go: after each range, brief_set and issue_set what it gave you before reading on, so the plan file holds your progress and nothing lives only in your context. A later page may correct an earlier value: set it again, and record the disagreement as a conflict. source_search finds clause numbers, ratings and keywords ("kV", "fault", "earthing", "ohm", "tilt", "pitch", "drawing list"); source_page_image checks scans, tables, site plans and any number that matters (OCR is imperfect). Open a page image only for a number you will record or a drawing you will trace, not to skim.
4. brief_schema lists the fields a plan must and should have for the segment, with where tenders usually state them. brief_set them in batches. Every value carries status and source: stated (quote it; source = document id, page, clause/table), inferred (derived; source + note saying how), assumed (a choice the documents do not make; note = what and why), not_applicable (note = why). Set extra fields too (any 'section.name' key) for contract requirements the schema does not list: anything that changes a drawing belongs in the brief.
5. issue_set every conflict between documents (options with sources, the decision and its reason), every gap the documents leave open (decide it when the documents allow a sound engineering choice; otherwise leave the decision to the build), and open points for the client.
6. equipment_set the module, inverter (or PCS/battery), transformer and other catalogued equipment the build starts from: the equipment the documents name (e.g. in a PVsyst or bid report), never a substitute you pick. It checks UniCAD's catalog. For equipment the catalog lacks, find the manufacturer's datasheet, datasheet_fetch it, and draft the entry from its text: catalog_entry_schema(kind) shows an existing entry to copy the shape from; every number comes from that datasheet, cited in source.ref with the URL, revision and page. plan_check refuses a datasheet URL that was not fetched or does not name the model, and an entry with empty or implausible values. If no datasheet can be found, say so in the equipment note and leave the entry out: never invent a URL, a model or a value.
7. Site (ground-mount): if the tender has a survey file, record it as site.site_file. Otherwise trace the site plan: source_page_image with a grid and crops to read pixel coordinates, site_preview to see your trace over the plan, and site_trace to write model/site.geojson, scaled from a known dimension or the stated areas and anchored at the site's lat/lon. Polygon areas must end within 2 % of the stated areas.
8. Use calculate for every number you derive (string Voc at minimum ambient, DC/AC ratio, areas, counts). Put checks the builder must repeat in reference_design.checks.
9. plan_check until ready (no errors); read the warnings and fix what you can. plan_write renders engineering-brief.md and AGENT_PROMPT.md. Tell the user the plan folder path, the decisions you made, what is ASSUMED and what is still open for the client.

Rules: the contract's clauses govern; a PVsyst report or bid summary is the reference design to check, not the contract: where both state a value (row pitch, DC/AC, POI) and they differ, record a conflict and decide it. Look at every landscape page and every drawing as an image (plan_check refuses the plan otherwise). Never state a value the documents do not give as fact; never drop a requirement because UniCAD may not support it (record it; the build reports tool gaps); keep the brief in the documents' own units and wording where it matters."""

PLAN_PROMPT = """Make a UniCAD build plan for "{name}" from the documents the user gave{files}.

Follow the UniPlan server instructions step by step. Read everything before deciding; quote sources; decide conflicts with reasons; trace the site when there is no survey; draft catalog entries for missing equipment from datasheets; plan_check until ready; then plan_write. Finish with the plan folder path, the decisions made, the ASSUMED values and the open points."""


class BriefItem(BaseModel):
    key: str = Field(description="'section.field', e.g. 'grid.export_voltage_v' (brief_schema lists them; other names in a known section are kept as extra fields)")
    value: Value = Field(description="The value as the documents give it: number, text, list or object")
    unit: str | None = None
    status: Literal["stated", "inferred", "assumed", "not_applicable"] = "stated"
    source: str | None = Field(None, description="Document id, page and clause/table, e.g. 'append-1-4 p.14 cl.132'")
    note: str | None = Field(None, description="inferred: how; assumed: what and why; not_applicable: why")


class OptionArg(BaseModel):
    value: str
    source: str | None = None


def _version() -> str:
    try:
        return metadata.version("uniplan")
    except metadata.PackageNotFoundError:
        return ""


def guarded(fn):
    """Domain errors become tool errors with their own wording."""
    import functools

    @functools.wraps(fn)
    def wrapper(*a, **kw):
        try:
            return fn(*a, **kw)
        except ToolError:
            raise
        except ValidationError as e:
            raise ToolError("; ".join(f"{'.'.join(str(x) for x in err['loc'])}: {err['msg']}" for err in e.errors())) from None
        except (ValueError, KeyError, OSError) as e:
            raise ToolError(str(e.args[0]) if e.args else repr(e)) from None

    return wrapper


def build_server(root: Path) -> MCPServer:
    store = PlanStore(root)
    server = MCPServer("uniplan", instructions=INSTRUCTIONS, version=_version())

    def status_dict() -> dict[str, Any]:
        if not store.exists():
            return {"plan": None, "plan_dir": str(store.root), "next": "plan_create(name, segment)"}
        plan = store.load()
        res = checks.check(plan, datasheets.reader(store))
        return {
            "plan": plan.name, "segment": plan.segment, "plan_dir": str(store.root),
            "sources": [{"id": s.id, "kind": s.kind, "pages": s.pages} for s in plan.sources],
            "fields_set": sorted(plan.fields), "issues": [f"{i.id} {i.kind}: {i.topic}" for i in plan.issues],
            "equipment": [f"{e.role}: {e.manufacturer} {e.model} ({e.catalog_id or 'not in catalog'})" for e in plan.equipment],
            "site": plan.site.geojson if plan.site else None, "written": plan.written,
            "ready": res["ready"], "errors": len(res["errors"]), "warnings": len(res["warnings"]),
        }  # fmt: skip

    # ---------------------------------------------------------------- plan
    @server.tool(annotations=READ_ONLY)
    @guarded
    def plan_status() -> dict[str, Any]:
        """What the plan folder holds: sources, fields set, issues, equipment, site, and whether plan_check passes. Start here."""
        return status_dict()

    @server.tool()
    @guarded
    def plan_create(
        name: Annotated[str, Field(description="Project name as the tender gives it, e.g. 'SCL Panipat 1.8 MWp ground-mounted solar PV'")],
        segment: Annotated[Literal["utility", "rooftop", "bess", "hybrid"], Field(description="utility: ground-mount PV; rooftop: C&I rooftop PV; bess: storage only; hybrid: PV + storage")],
    ) -> dict[str, Any]:
        """Start a plan in the plan folder (one plan per folder)."""
        store.create(name, segment)
        return status_dict()

    # ---------------------------------------------------------------- sources
    @server.tool()
    @guarded
    def source_add(
        file_path: Annotated[str, Field(description="A PDF, image or text file on this computer; it is copied into tender/")],
        title: Annotated[str | None, Field(description="What the document is, e.g. 'Technical Annex 2-19 with site plan and PVsyst'")] = None,
        ocr: Annotated[bool, Field(description="OCR scanned pages when Tesseract is installed")] = True,
    ) -> dict[str, Any]:
        """Add a tender / PRD document to the plan: copies it into tender/, extracts the text per page, finds scanned and landscape pages."""
        s, notes = sources.add_source(store, file_path, title, ocr)
        return {"source": s.model_dump(exclude_none=True), "notes": notes}

    @server.tool(annotations=READ_ONLY)
    @guarded
    def source_list() -> list[dict[str, Any]]:
        """The documents in the plan with their page counts, scanned and landscape pages."""
        return [s.model_dump(exclude_none=True) for s in store.load().sources]

    @server.tool(annotations=READ_ONLY)
    @guarded
    def source_read(
        doc: Annotated[str, Field(description="Source id from source_add / source_list")],
        pages: Annotated[str | None, Field(description="e.g. '1-5' or '3,7,9-10'; all pages when omitted")] = None,
        max_chars: Annotated[int, Field(ge=1000, le=100000)] = 30000,
    ) -> dict[str, Any]:
        """The text of a document, page by page (page markers included). Long reads stop at max_chars and say where to continue."""
        return sources.read_pages(store, doc, pages, max_chars)

    @server.tool(annotations=READ_ONLY)
    @guarded
    def source_search(
        query: Annotated[str, Field(description="Text to find (case-insensitive), or a regular expression with regex=true")],
        doc: Annotated[str | None, Field(description="Limit to one source id")] = None,
        regex: bool = False,
        context: Annotated[int, Field(ge=0, le=1000, description="Characters of context on each side")] = 150,
        limit: Annotated[int, Field(ge=1, le=200)] = 40,
    ) -> list[dict[str, Any]]:
        """Find where the documents say something: returns doc, page and a snippet per hit."""
        return sources.search(store, query, doc, regex, context, limit)

    @server.tool(annotations=READ_ONLY, structured_output=False)
    @guarded
    def source_page_image(
        doc: Annotated[str, Field(description="Source id")],
        page: Annotated[int, Field(ge=1, description="Page number (1 for an image source)")] = 1,
        rotate: Annotated[Literal[0, 90, 180, 270], Field(description="Clockwise rotation, to read a landscape page upright")] = 0,
        crop: Annotated[list[float] | None, Field(description="[x0, y0, x1, y1] in pixels of the full (rotated) page image, to zoom in")] = None,
        grid: Annotated[float | None, Field(description="Draw a labelled grid every N pixels of the full image (e.g. 100), to read coordinates for site_trace")] = None,
    ) -> list[Any]:
        """A page as an image, to read scans, tables, drawings and site plans. The full page image (150 dpi) is saved under tender/pages; crop
        and grid coordinates are in its pixels, which are the coordinates site_trace and site_preview take."""
        s = sources.get_source(store, doc)
        path = sources.page_image_path(store, s, page, rotate)
        png, info = sources.render_view(path, crop, grid)
        plan = store.load()
        for src in plan.sources:
            if src.id == s.id and page not in src.viewed_pages:
                src.viewed_pages = sorted(src.viewed_pages + [page])
                store.save(plan)
        return [json.dumps({"image": store.rel(path), **info}), Image(data=png, format="png")]

    # ---------------------------------------------------------------- brief
    @server.tool(annotations=READ_ONLY)
    @guarded
    def brief_schema() -> list[dict[str, Any]]:
        """The brief's sections and fields for this plan's segment: level must/should, and where tenders usually state each one."""
        return schema_for(store.load().segment)

    @server.tool()
    @guarded
    def brief_set(items: Annotated[list[BriefItem], Field(description="One or more fields to set (replaces a field already set)")]) -> dict[str, Any]:
        """Set brief fields with their status and source. Stated values need a source; inferred ones a source and a note; assumed ones a note."""
        plan = store.load()
        done, warn = [], []
        for it in items:
            if "." not in it.key or it.key.split(".", 1)[0] not in SECTION_KEYS:
                raise ValueError(f"{it.key}: keys are 'section.field' with section one of {', '.join(sorted(SECTION_KEYS))}")
            plan.fields[it.key] = FieldValue(value=it.value, unit=it.unit, status=it.status, source=it.source, note=it.note)
            done.append(it.key)
            if it.status == "stated" and not it.source:
                warn.append(f"{it.key}: no source")
        store.save(plan)
        return {"set": done, "warnings": warn, "fields": len(plan.fields)}

    @server.tool()
    @guarded
    def brief_remove(keys: list[str]) -> dict[str, Any]:
        """Remove brief fields."""
        plan = store.load()
        gone = [k for k in keys if plan.fields.pop(k, None) is not None]
        store.save(plan)
        return {"removed": gone}

    @server.tool()
    @guarded
    def issue_set(
        kind: Annotated[Literal["conflict", "gap", "open_point"], Field(description="conflict: documents disagree; gap: the documents leave a design input open; open_point: a question for the client")],
        topic: Annotated[str, Field(description="Short name, e.g. 'DC/AC ratio'")],
        detail: str = "",
        options: Annotated[list[OptionArg] | None, Field(description="Conflicts: each competing value with its source")] = None,
        decision: Annotated[str | None, Field(description="The value / approach the build must use")] = None,
        reason: Annotated[str | None, Field(description="Why this decision")] = None,
        fields: Annotated[list[str] | None, Field(description="Brief fields this issue affects")] = None,
        id: Annotated[str | None, Field(description="Update this issue instead of adding one")] = None,
    ) -> dict[str, Any]:
        """Record a conflict between documents, a gap the documents leave open, or an open point for the client."""
        plan = store.load()
        iss = Issue(id=id or f"I{len(plan.issues) + 1}", kind=kind, topic=topic, detail=detail, decision=decision, reason=reason,
                    options=[Option(**o.model_dump()) for o in options or []], fields=fields or [])  # fmt: skip
        if id:
            for n, old in enumerate(plan.issues):
                if old.id == id:
                    plan.issues[n] = iss
                    break
            else:
                raise ValueError(f"no issue {id}")
        else:
            while any(i.id == iss.id for i in plan.issues):
                iss.id = f"I{int(iss.id[1:]) + 1}"
            plan.issues.append(iss)
        store.save(plan)
        return {"issue": iss.model_dump(exclude_none=True), "issues": len(plan.issues)}

    # ---------------------------------------------------------------- equipment
    @server.tool()
    @guarded
    def equipment_set(
        role: Annotated[str, Field(description="module, inverter, pcs, battery, transformer, or another role (e.g. aux_transformer); one item per role")],
        kind: Annotated[Literal["module", "inverter", "pcs", "battery", "transformer", "cable", "protection", "meter", "logger"], Field(description="UniCAD catalog kind")],
        manufacturer: str,
        model: str,
        source: Annotated[str | None, Field(description="Where the documents name it")] = None,
        datasheet: Annotated[str | None, Field(description="For equipment the catalog lacks: the exact URL fetched with datasheet_fetch")] = None,
        entry: Annotated[dict[str, Any] | None, Field(description="Drafted UniCAD catalog entry (shape from catalog_entry_schema), every value from the datasheet")] = None,
        note: str | None = None,
    ) -> dict[str, Any]:
        """Record equipment the build starts from and check it against UniCAD's catalog. Missing items with a drafted entry go to catalog/needs.json for catalog_add."""
        plan = store.load()
        found = catalog.find(kind, manufacturer, model)
        e = Equipment(role=role, kind=kind, manufacturer=manufacturer, model=model, source=source, datasheet=datasheet, entry=entry, note=note,
                      catalog_id=found if isinstance(found, str) else None)  # fmt: skip
        problems: list[str] = []
        if entry is not None:
            ex = catalog.example_entry(kind)
            if ex:
                missing = [k for k in ex if k not in entry]
                if missing:
                    problems.append(f"entry lacks fields an existing {kind} entry has: {', '.join(missing)}")
            if not (isinstance(entry.get("source"), dict) and entry["source"].get("ref")):
                problems.append("entry.source.ref must cite the datasheet (URL, revision, page)")
        plan.equipment = [x for x in plan.equipment if x.role != role] + [e]
        store.save(plan)
        _write_needs(store, plan)
        status = "in catalog" if e.catalog_id else ("catalog not available to check" if found is None else "not in catalog")
        return {"equipment": e.model_dump(exclude_none=True, exclude={"entry"}), "catalog": status, "problems": problems,
                **({"next": "draft its catalog entry from the datasheet (catalog_entry_schema shows the shape) and equipment_set again with entry=..."} if found is False and not entry else {})}  # fmt: skip

    @server.tool()
    @guarded
    def datasheet_fetch(
        url: Annotated[str, Field(description="The manufacturer's datasheet: a PDF or product page URL you found (not a search page)")],
        max_chars: Annotated[int, Field(ge=1000, le=80000)] = 30000,
    ) -> dict[str, Any]:
        """Download a datasheet into catalog/datasheets/ and return its text. equipment_set only accepts a datasheet URL fetched here
        whose text names the model, so every drafted catalog value can be traced to a real document."""
        ds, text = datasheets.fetch(store, url)
        return {"datasheet": ds.model_dump(), "text": text[:max_chars], **({"truncated": True} if len(text) > max_chars else {})}

    @server.tool(annotations=READ_ONLY)
    @guarded
    def catalog_search(
        kind: Annotated[Literal["module", "inverter", "pcs", "battery", "transformer", "cable", "protection", "meter", "logger"], Field()],
        query: Annotated[str, Field(description="Words of the make / model, e.g. 'sungrow 1100'")],
    ) -> dict[str, Any]:
        """Search UniCAD's catalog (read-only) for equipment the documents name."""
        return catalog.search(kind, query)

    @server.tool(annotations=READ_ONLY)
    @guarded
    def catalog_entry_schema(kind: Annotated[Literal["module", "inverter", "pcs", "battery", "transformer", "cable", "protection", "meter", "logger"], Field()]) -> dict[str, Any]:
        """One existing UniCAD catalog entry of this kind: copy its shape (every field) when drafting an entry for missing equipment."""
        ex = catalog.example_entry(kind)
        if ex is None:
            raise ValueError("UniCAD's catalog was not found (set UNICAD_CATALOG): draft the entry from the datasheet's own fields and say so")
        return {"kind": kind, "example": ex}

    # ---------------------------------------------------------------- site
    @server.tool(annotations=READ_ONLY, structured_output=False)
    @guarded
    def site_preview(
        doc: Annotated[str, Field(description="Source id holding the site plan")],
        features: list[TraceFeature],
        page: int = 1,
        rotate: Literal[0, 90, 180, 270] = 0,
        crop: Annotated[list[float] | None, Field(description="[x0, y0, x1, y1] to zoom on part of the plan")] = None,
    ) -> list[Any]:
        """Draw traced features over the site plan (nothing is saved), to check the trace follows the plan before site_trace."""
        s = sources.get_source(store, doc)
        path = sources.page_image_path(store, s, page, rotate)
        return [json.dumps({"image": store.rel(path)}), Image(data=preview(path, features, crop), format="png")]

    @server.tool(structured_output=False)
    @guarded
    def site_trace(
        doc: Annotated[str, Field(description="Source id holding the site plan")],
        features: Annotated[list[TraceFeature], Field(description="Boundaries (usable areas), exclusions, lines (transmission line with RoW width, roads) traced in pixels of the page image")],
        anchor_lat: Annotated[float, Field(description="Latitude the site sits at (e.g. the PVsyst site point)")],
        anchor_lon: Annotated[float, Field(description="Longitude the site sits at")],
        page: int = 1,
        rotate: Literal[0, 90, 180, 270] = 0,
        m_per_px: Annotated[float | None, Field(description="Known scale; otherwise known_distance or the stated areas set it")] = None,
        known_distance: Annotated[KnownDistance | None, Field(description="Two pixel points and the metres between them (a dimension on the plan)")] = None,
        anchor_px: Annotated[list[float] | None, Field(description="Pixel that sits at the anchor lat/lon; default the centroid of the boundary features")] = None,
        notes: Annotated[list[str] | None, Field(description="What the build must know about the trace (north assumed up, RoW from a dimension, ...)")] = None,
    ) -> list[Any]:
        """Write model/site.geojson from a traced site plan (names marked ASSUMED, kinds UniCAD's site_import reads) and record it in the plan.
        Returns the area check and the trace drawn over the plan."""
        s = sources.get_source(store, doc)
        path = sources.page_image_path(store, s, page, rotate)
        gj, report = trace(features, anchor_lon, anchor_lat, m_per_px, known_distance, anchor_px)
        out = store.root / "model" / "site.geojson"
        write_json(out, gj)
        plan = store.load()
        plan.site = site_plan(store.rel(path), store.rel(out), anchor_lon, anchor_lat, report, notes or [])
        store.save(plan)
        return [json.dumps({"geojson": store.rel(out), **report}), Image(data=preview(path, features, None), format="png")]

    # ---------------------------------------------------------------- compute, check, write
    @server.tool(annotations=READ_ONLY)
    @guarded
    def calculate(
        expression: Annotated[str, Field(description="Arithmetic with + - * / ** and math functions (sqrt, log, ceil, ...); 'acre' = 4046.856 m2")],
        variables: Annotated[dict[str, float] | None, Field(description="Named values used in the expression")] = None,
    ) -> dict[str, Any]:
        """Compute a value exactly, e.g. string Voc at minimum ambient: 28 * 49.9 * (1 + (-0.28/100) * (0 - 25))."""
        return {"expression": expression, "result": calc.evaluate(expression, variables)}

    @server.tool(annotations=READ_ONLY)
    @guarded
    def plan_check() -> dict[str, Any]:
        """Is the plan ready to hand to a build model? Errors (missing must-fields, unsourced values, undecided conflicts, site areas off) block plan_write."""
        return checks.check(store.load(), datasheets.reader(store))

    @server.tool()
    @guarded
    def plan_write(
        work_dir: Annotated[str | None, Field(description="Folder the build will work in, as the prompt should name it; default the plan folder")] = None,
        unicad_template: Annotated[str | None, Field(description="network_autobuild template to suggest; default derived from segment, export voltage and inverter type")] = None,
        extra_rules: Annotated[list[str] | None, Field(description="Project-specific rules of work for the build model")] = None,
    ) -> dict[str, Any]:
        """Render engineering-brief.md and AGENT_PROMPT.md (and catalog/needs.json) once plan_check has no errors."""
        plan = store.load()
        res = checks.check(plan, datasheets.reader(store))
        if not res["ready"]:
            raise ValueError("plan_check has errors; fix them first: " + " | ".join(res["errors"][:12]))
        import datetime as dt

        work = work_dir or str(store.root)
        brief_path, prompt_path = store.root / "engineering-brief.md", store.root / "AGENT_PROMPT.md"
        write_text(brief_path, render.brief(plan))
        write_text(prompt_path, render.prompt(plan, work, unicad_template, extra_rules or []))
        _write_needs(store, plan)
        plan.written = dt.datetime.now().isoformat(timespec="seconds")
        store.save(plan)
        return {"brief": str(brief_path), "prompt": str(prompt_path), "needs": str(store.root / "catalog" / "needs.json"),
                "template": unicad_template or render.suggest_template(plan), "warnings": res["warnings"]}  # fmt: skip

    @server.prompt()
    def plan_from_documents(name: str = "Solar project", files: str = "") -> str:
        """Step plan for turning tender / PRD documents into a UniCAD build plan."""
        return PLAN_PROMPT.format(name=name, files=f" ({files})" if files else "")

    return server


def _write_needs(store: PlanStore, plan) -> None:
    needs = [
        {"role": e.role, "kind": e.kind, "manufacturer": e.manufacturer, "model": e.model, "datasheet": e.datasheet, "entry": e.entry}
        for e in plan.equipment
        if not e.catalog_id
    ]
    write_json(store.root / "catalog" / "needs.json", needs)
