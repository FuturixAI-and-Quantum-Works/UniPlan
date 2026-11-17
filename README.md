# UniPlan

An MCP server that turns a tender, RFP or PRD into a **build plan** for [UniCAD](https://github.com/FuturixAI-and-Quantum-Works/UniCAD).

The split it is built for:

| | Super mode (planning) | Build mode |
|---|---|---|
| Model | a strong model | any model, small ones included |
| MCP server | UniPlan only | UniCAD only |
| Input | the tender / PRD | the plan folder |
| Output | the plan folder, gated by `plan_check` | drawings and schedules, gated by UniCAD's `validate` |
| Context | its own | fresh: the build never sees the planning conversation |

The planner reads every document, records each contract value with its source (or marks it inferred / ASSUMED with a reason),
decides conflicts between documents, traces the site plan when there is no survey, drafts catalog entries for equipment UniCAD lacks,
and writes a prompt a build model can follow stage by stage without ever opening the tender.

## The plan folder

```
plan.json               structured plan: the source of truth
tender/                 the documents as given, <doc>.txt (text per page, OCR for scans), pages/ (page images)
engineering-brief.md    every value with its source; conflicts decided; gaps; open points
AGENT_PROMPT.md         the build task: inputs, design intent, equipment, decisions, stages with done-checks, rules, escalation, deliverables
model/site.geojson      the digitised site (every feature ASSUMED), ready for UniCAD's site_import
catalog/needs.json      equipment UniCAD's catalog lacks, with entries drafted from datasheets, for catalog_add
```

## Tools

| Tool | Does |
|---|---|
| `plan_status`, `plan_create` | what the folder holds; start a plan (utility, rooftop, bess, hybrid) |
| `source_add`, `source_list`, `source_read`, `source_search` | add documents (PDF, images, text; OCR when Tesseract is installed), read by page, search with context |
| `source_page_image` | a page as an image, rotated, cropped, with a labelled pixel grid for tracing |
| `brief_schema`, `brief_set`, `brief_remove` | the fields a plan must / should have; set them with status (stated / inferred / assumed / not_applicable) and source |
| `issue_set` | conflicts (options, decision, reason), gaps, open points |
| `equipment_set`, `catalog_search`, `catalog_entry_schema` | equipment the build starts from, checked against UniCAD's catalog; drafted entries for what it lacks |
| `site_preview`, `site_trace` | trace a scanned site plan into GeoJSON, scaled from a dimension or the stated areas, anchored at the site lat/lon |
| `calculate` | exact arithmetic (string Voc, ratios, areas) |
| `plan_check`, `plan_write` | the gate (missing must-fields, unsourced values, undecided conflicts, site areas off) and the render |

## Run

```bash
uv run uniplan-mcp --plan-dir ./plan
```

`UNIPLAN_DIR` sets the plan folder; `UNICAD_CATALOG` points at UniCAD's catalog. Installed in the same Python as UniCAD (the runtime
OpenCAD ships), UniPlan finds UniCAD's bundled catalog by itself.

## Tests

```bash
uv run pytest
```
