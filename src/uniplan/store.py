"""The plan model (plan.json) and the plan folder it lives in.

Folder layout, all relative to the plan directory:
  plan.json                 the structured plan (source of truth; the Markdown files are rendered from it)
  tender/                   source documents as given, with <doc>.txt (text per page) and pages/ (rendered page images)
  model/site.geojson        the digitised site, when one was traced
  catalog/needs.json        equipment the build needs that UniCAD's catalog lacks, with datasheet data
  engineering-brief.md      rendered brief
  AGENT_PROMPT.md           rendered build prompt
"""

import datetime as dt
import json
import os
import tempfile
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

Status = Literal["stated", "inferred", "assumed", "not_applicable"]
Value = str | float | int | bool | list[Any] | dict[str, Any]


class FieldValue(BaseModel):
    value: Value
    unit: str | None = None
    status: Status = "stated"
    source: str | None = None
    note: str | None = None


class Option(BaseModel):
    value: str
    source: str | None = None


class Issue(BaseModel):
    id: str
    kind: Literal["conflict", "gap", "open_point"]
    topic: str
    detail: str = ""
    options: list[Option] = Field(default_factory=list)
    decision: str | None = None
    reason: str | None = None
    fields: list[str] = Field(default_factory=list)


class Source(BaseModel):
    id: str
    file: str
    kind: Literal["pdf", "image", "text"]
    title: str | None = None
    pages: int = 1
    text_file: str | None = None
    scanned_pages: list[int] = Field(default_factory=list)
    landscape_pages: list[int] = Field(default_factory=list)
    viewed_pages: list[int] = Field(default_factory=list)
    read_pages: list[int] = Field(default_factory=list)
    ocr: bool = False


class Datasheet(BaseModel):
    url: str
    file: str
    text_file: str
    fetched: str
    bytes: int


class Equipment(BaseModel):
    role: str
    kind: str
    manufacturer: str
    model: str
    source: str | None = None
    catalog_id: str | None = None
    datasheet: str | None = None
    entry: dict[str, Any] | None = None
    note: str | None = None


class SiteFeature(BaseModel):
    name: str
    kind: str
    area_m2: float | None = None
    stated_area_m2: float | None = None
    length_m: float | None = None


class SitePlan(BaseModel):
    image: str
    geojson: str
    anchor_lon: float
    anchor_lat: float
    m_per_px: float
    scale_from: str
    features: list[SiteFeature]
    notes: list[str] = Field(default_factory=list)


class Plan(BaseModel):
    schema_version: int = 1
    name: str
    segment: Literal["utility", "rooftop", "bess", "hybrid"]
    created: str
    sources: list[Source] = Field(default_factory=list)
    fields: dict[str, FieldValue] = Field(default_factory=dict)
    issues: list[Issue] = Field(default_factory=list)
    equipment: list[Equipment] = Field(default_factory=list)
    site: SitePlan | None = None
    datasheets: list[Datasheet] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
    written: str | None = None


class PlanStore:
    def __init__(self, root: Path):
        self.root = root.expanduser().resolve()

    @property
    def plan_file(self) -> Path:
        return self.root / "plan.json"

    def exists(self) -> bool:
        return self.plan_file.is_file()

    def load(self) -> Plan:
        if not self.exists():
            raise ValueError(f"no plan in {self.root}: call plan_create first")
        return Plan.model_validate_json(self.plan_file.read_text(encoding="utf-8"))

    def save(self, plan: Plan) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        write_text(self.plan_file, plan.model_dump_json(indent=1, exclude_none=True) + "\n")

    def create(self, name: str, segment: str) -> Plan:
        if self.exists():
            raise ValueError(f"{self.plan_file} already exists: this folder already holds a plan (plan_status shows it)")
        for sub in ("tender/pages", "model", "catalog"):
            (self.root / sub).mkdir(parents=True, exist_ok=True)
        plan = Plan(name=name, segment=segment, created=dt.date.today().isoformat())  # type: ignore[arg-type]
        self.save(plan)
        return plan

    def path(self, rel: str) -> Path:
        """A path inside the plan folder; absolute paths and '..' escapes are refused."""
        p = (self.root / rel).resolve()
        if self.root not in p.parents and p != self.root:
            raise ValueError(f"{rel} is outside the plan folder")
        return p

    def rel(self, p: Path) -> str:
        return p.resolve().relative_to(self.root).as_posix()


def write_text(path: Path, text: str) -> None:
    """Write via a temporary file and rename, so a crash never leaves half a file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def write_json(path: Path, data: Any) -> None:
    write_text(path, json.dumps(data, indent=1, ensure_ascii=False) + "\n")
