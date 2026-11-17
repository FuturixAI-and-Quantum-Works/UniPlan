"""What a plan must say: brief sections and their fields, per project segment.

Every field has a level: "must" (plan_check refuses the plan without it, unless it is set not_applicable with a reason) or "should"
(a warning). `where` tells the planner where a tender usually states it. Fields not listed here may still be set: they are kept
as extra items of their section.
"""

from dataclasses import dataclass
from typing import Literal

Level = Literal["must", "should"]
SEGMENTS = ("utility", "rooftop", "bess", "hybrid")


@dataclass(frozen=True)
class FieldSpec:
    key: str
    label: str
    level: Level
    where: str
    segments: tuple[str, ...] = SEGMENTS
    unit: str | None = None


@dataclass(frozen=True)
class Section:
    key: str
    title: str
    fields: tuple[FieldSpec, ...]


PV = ("utility", "rooftop", "hybrid")
GROUND = ("utility", "hybrid")
STORAGE = ("bess", "hybrid")


def F(key: str, label: str, level: Level, where: str, segments: tuple[str, ...] = SEGMENTS, unit: str | None = None) -> FieldSpec:
    return FieldSpec(key, label, level, where, segments, unit)


SECTIONS: tuple[Section, ...] = (
    Section("project", "Project", (
        F("name", "Project name", "must", "cover page, tender title"),
        F("client", "Client / owner", "must", "cover page, contract parties"),
        F("epc", "EPC contractor (who issues the drawings)", "must", "contract parties, bid cover letter"),
        F("location", "Location (plant, town, state)", "must", "cover page, site description"),
        F("stage", "Design stage (bid, detailed engineering, as-built)", "should", "scope of work"),
        F("scope_split", "Owner vs EPC scope split that changes the design (owner-supplied items, battery limits)", "should", "scope matrix, technical spec"),
    )),
    Section("capacity", "Capacity", (
        F("dc_kwp", "DC capacity", "must", "technical spec, PVsyst report", PV, "kWp"),
        F("ac_kw", "AC capacity", "must", "technical spec", PV, "kW"),
        F("dc_ac_ratio", "DC/AC ratio (and the band the contract allows)", "should", "technical spec, design basis", PV),
        F("bess_kw", "Storage power", "must", "technical spec", STORAGE, "kW"),
        F("bess_kwh", "Storage energy", "must", "technical spec", STORAGE, "kWh"),
    )),
    Section("grid", "Grid connection", (
        F("export_voltage_v", "Export / hook-up voltage", "must", "technical spec, single-line diagram", unit="V"),
        F("poi", "Point of interconnection / terminal point (the contract's wording, e.g. a named breaker)", "must", "scope of work, battery limits, terminal point clause"),
        F("fault_levels", "Fault level per system voltage, with duration", "must", "design basis (switchgear, cables)"),
        F("frequency_hz", "Grid frequency", "should", "technical spec", unit="Hz"),
        F("variation", "Voltage and frequency variation", "should", "technical spec"),
        F("export_line", "Line from the plant to the POI (voltage, size, length)", "should", "PVsyst, scope of work"),
    )),
    Section("site", "Site and climate", (
        F("latitude", "Latitude", "must", "PVsyst report, site description", unit="deg"),
        F("longitude", "Longitude", "must", "PVsyst report, site description", unit="deg"),
        F("altitude_m", "Altitude", "should", "PVsyst report", unit="m"),
        F("ambient_max_c", "Design maximum ambient", "must", "design basis", unit="degC"),
        F("ambient_min_c", "Minimum ambient (for string Voc)", "must", "design basis, met data; often missing: decide and mark ASSUMED", unit="degC"),
        F("land", "Land / roof area available and how it is split", "must", "site plan, technical spec", PV),
        F("site_plan", "Site plan source (document and page) and what it shows", "must", "annexures"),
        F("exclusions", "Areas that may not be used (RoW, shading, buildings) and why", "should", "site plan notes"),
        F("setbacks", "Setbacks and clearances", "should", "design basis, local rules"),
        F("soil_resistivity", "Soil resistivity (or that it is not given)", "should", "geotech / ERT report", GROUND),
    )),
    Section("pv", "PV array", (
        F("module", "Module make, model and Wp", "must", "technical spec, PVsyst", PV),
        F("module_count", "Number of modules", "should", "PVsyst", PV),
        F("modules_per_string", "Modules per string", "must", "PVsyst, string sizing", PV),
        F("strings", "Number of strings", "should", "PVsyst", PV),
        F("mounting", "Mounting: fixed tilt / tracker / rooftop, tilt and azimuth", "must", "technical spec, MMS spec", PV),
        F("table_format", "Table format (e.g. 2P x 28) and allowed small tables", "should", "MMS spec, design basis", PV),
        F("row_pitch", "Row pitch, table gap, module gap", "should", "MMS spec, design basis (a contract clause governs over the PVsyst shed spacing; if they differ it is a conflict)", PV),
        F("ground_clearance", "Minimum ground clearance", "should", "MMS spec", GROUND),
    )),
    Section("dc", "DC collection", (
        F("combiner", "String combiner / SMB / SCB: inputs, rating, monitoring, protection", "should", "technical spec, design basis", PV),
        F("dc_cables", "DC cable specification and sizing rule", "should", "design basis (cables)", PV),
        F("dc_loss_max", "DC loss limit", "must", "design basis", PV, "%"),
    )),
    Section("conversion", "Inverters / PCS", (
        F("inverter", "Inverter make, model, type (string/central) and count", "must", "technical spec, PVsyst, approved vendors", PV),
        F("inverter_rating", "Inverter rating at design ambient, output voltage, MPPT window", "should", "technical spec, datasheet", PV),
        F("pcs", "PCS make, model, rating and count", "must", "technical spec", STORAGE),
        F("battery", "Battery make, chemistry, container", "must", "technical spec", STORAGE),
    )),
    Section("ac", "AC system", (
        F("transformer", "Transformers: rating, ratio, impedance, cooling, vector group, taps", "should", "technical spec, design basis"),
        F("switchgear", "MV / LV panels: type, breaker, ratings", "should", "technical spec, design basis"),
        F("protection", "Protection and metering functions", "should", "design basis (protection)"),
        F("ac_cables", "AC / HT cable specification and sizing rule", "should", "design basis (cables)"),
        F("ac_loss_max", "AC loss limit", "must", "design basis", unit="%"),
        F("auxiliary", "Auxiliary supply (aux transformer, panels, UPS) and its feed point", "should", "design basis (auxiliary)"),
    )),
    Section("earthing", "Earthing and lightning", (
        F("earthing", "Earthing design basis: target resistance, strips, pits, standard", "must", "design basis (earthing)"),
        F("lps", "Lightning protection: level, method, standard, pits", "must", "design basis (LPS), technical spec"),
    )),
    Section("plant", "Other plant", (
        F("civil", "Roads, drainage, fencing, control room, yards", "should", "civil scope, design basis"),
        F("scada", "SCADA, WMS, communication", "should", "technical spec"),
        F("other_systems", "Cleaning, fire, CCTV, lighting and other systems", "should", "technical spec"),
    )),
    Section("deliverables", "Deliverables", (
        F("drawing_list", "Drawings and documents the contract asks for: a list with every item as the contract lists it, by package", "must", "drawing / document list annexure"),
        F("vendors", "Approved vendors that constrain equipment choice", "should", "approved vendor list"),
        F("issue", "Issue purpose and revision (e.g. approval, rev 0) and title-block names", "should", "contract, drawing procedure"),
    )),
    Section("reference_design", "Reference (bid) design to check", (
        F("summary", "The bid / reference design the build starts from (PVsyst etc.) and its results", "should", "PVsyst report, bid"),
        F("checks", "Checks the builder must make on it (string Voc, MPPT window, fit on site, ratios)", "should", "derived from the above"),
    )),
)

SECTION_KEYS = {s.key for s in SECTIONS}
FIELD_SPECS: dict[str, FieldSpec] = {f"{s.key}.{f.key}": f for s in SECTIONS for f in s.fields}


def applies(spec: FieldSpec, segment: str) -> bool:
    return segment in spec.segments


def schema_for(segment: str) -> list[dict]:
    return [
        {
            "section": s.key,
            "title": s.title,
            "fields": [
                {"key": f"{s.key}.{f.key}", "label": f.label, "level": f.level, "where": f.where, **({"unit": f.unit} if f.unit else {})}
                for f in s.fields
                if applies(f, segment)
            ],
        }
        for s in SECTIONS
    ]
