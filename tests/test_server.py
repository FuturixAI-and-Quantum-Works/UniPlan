"""End to end through the MCP tool layer: a small tender PDF and a site plan image become a plan folder."""

import asyncio
import json
from pathlib import Path

import pymupdf
import pytest
from PIL import Image, ImageDraw

from uniplan import calc
from uniplan.server import build_server

ACRE = 4046.8564224


def call(_server, _tool, **args):
    try:
        res = asyncio.run(_server.call_tool(_tool, args))
    except Exception as e:
        raise RuntimeError(str(e)) from e
    if getattr(res, "is_error", False):
        raise RuntimeError(res.content[0].text)
    return res


def data(res):
    if res.structured_content is not None:
        sc = res.structured_content
        return sc.get("result", sc) if isinstance(sc, dict) and set(sc) == {"result"} else sc
    return json.loads(res.content[0].text)


@pytest.fixture
def tender(tmp_path: Path) -> Path:
    pdf = tmp_path / "Spec.pdf"
    doc = pymupdf.open()
    p = doc.new_page()
    p.insert_text((72, 72), "Technical specification\nDC capacity 1.80 MWp\nHook-up voltage 6.6 kV\nFault level 40 kA for 1 s")
    p = doc.new_page()
    p.insert_text((72, 72), "Design basis clause 132: DC loss < 2.5 %\nClause 140: AC loss < 0.5 %")
    doc.new_page(width=842, height=595)  # landscape scan: no text
    doc.save(pdf)
    return pdf


@pytest.fixture
def plan_png(tmp_path: Path) -> Path:
    im = Image.new("RGB", (400, 300), "white")
    d = ImageDraw.Draw(im)
    d.rectangle([50, 50, 250, 150], outline="black", width=2)
    d.rectangle([270, 50, 350, 100], outline="black", width=2)
    out = tmp_path / "site.png"
    im.save(out)
    return out


class FakeResponse:
    def __init__(self, body: bytes, url: str):
        self.body, self.url, self.headers = body, url, {"content-type": "application/pdf"}

    def read(self, n=-1):
        return self.body

    def geturl(self):
        return self.url

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def datasheet_pdf(model: str) -> bytes:
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 72), f"Datasheet {model}\n" + "Electrical characteristics: Voc Isc Vmp Imp\n" * 8)
    return doc.tobytes()


@pytest.fixture
def web(monkeypatch):
    pages = {
        "https://example.com/goldi.pdf": datasheet_pdf("GS10-B144-GF-550"),
        "https://example.com/inv.pdf": datasheet_pdf("SG1100UD-20"),
    }

    def urlopen(req, timeout=None):
        url = req.full_url
        if url not in pages:
            import urllib.error

            raise urllib.error.HTTPError(url, 404, "not found", {}, None)
        return FakeResponse(pages[url], url)

    monkeypatch.setattr("uniplan.datasheets.urllib.request.urlopen", urlopen)


def test_tools_listed(tmp_path):
    s = build_server(tmp_path / "plan")
    names = {t.name for t in asyncio.run(s.list_tools())}
    assert {"plan_create", "source_add", "brief_set", "site_trace", "plan_check", "plan_write"} <= names


def test_full_flow(tmp_path, tender, plan_png, web):
    root = tmp_path / "plan"
    s = build_server(root)
    call(s, "plan_create", name="Test 1.8 MWp", segment="utility")
    added = data(call(s, "source_add", file_path=str(tender), title="Spec", ocr=False))
    assert added["source"]["pages"] == 3
    assert 3 in added["source"]["scanned_pages"] and 3 in added["source"]["landscape_pages"]
    call(s, "source_add", file_path=str(plan_png), title="site plan")

    hits = data(call(s, "source_search", query="6.6 kV"))
    assert hits and hits[0]["page"] == 1
    text = data(call(s, "source_read", doc="Spec", pages="2"))
    assert "Clause 140" in text["text"]

    img = call(s, "source_page_image", doc="Spec", page=1, grid=100)
    assert any(c.type == "image" for c in img.content)
    assert (root / "tender" / "pages" / "Spec-p001.png").is_file()

    # plan not ready yet
    chk = data(call(s, "plan_check"))
    assert not chk["ready"] and any("grid.export_voltage_v" in e for e in chk["errors"])
    with pytest.raises(RuntimeError, match="plan_check has errors"):
        call(s, "plan_write")

    src = "Spec p.1"
    items = [
        {"key": "project.name", "value": "Test 1.8 MWp", "source": src},
        {"key": "project.client", "value": "Client Ltd", "source": src},
        {"key": "project.epc", "value": "EPC Ltd", "source": src},
        {"key": "project.location", "value": "Panipat, Haryana", "source": src},
        {"key": "capacity.dc_kwp", "value": 1800, "unit": "kWp", "source": src},
        {"key": "capacity.ac_kw", "value": 1100, "unit": "kW", "source": src},
        {"key": "grid.export_voltage_v", "value": "6.6 kV", "source": src},
        {"key": "grid.poi", "value": "HT panel outgoing breaker", "source": src},
        {"key": "grid.fault_levels", "value": {"6.6 kV": "40 kA 1 s"}, "source": src},
        {"key": "site.latitude", "value": 29.4, "source": src},
        {"key": "site.longitude", "value": 76.89, "source": src},
        {"key": "site.ambient_max_c", "value": 50, "source": src},
        {"key": "site.ambient_min_c", "value": 0, "status": "assumed", "note": "Panipat winter minimum; documents silent"},
        {"key": "site.land", "value": "Block 1 + Block 2", "source": "site p.1"},
        {"key": "site.site_plan", "value": "site.png", "source": "site p.1"},
        {"key": "pv.module", "value": "Goldi GS10-B144-GF-550", "source": src},
        {"key": "pv.modules_per_string", "value": 28, "source": src},
        {"key": "pv.mounting", "value": "fixed tilt 5 deg south", "source": src},
        {"key": "dc.dc_loss_max", "value": 2.5, "unit": "%", "source": "Spec p.2 cl.132"},
        {"key": "conversion.inverter", "value": "1 x central Sungrow SG1100UD-20", "source": src},
        {"key": "ac.ac_loss_max", "value": 0.5, "unit": "%", "source": "Spec p.2 cl.140"},
        {"key": "earthing.earthing", "value": "< 1 ohm", "source": src},
        {"key": "earthing.lps", "value": "ESE level IV", "source": src},
        {"key": "deliverables.drawing_list", "value": ["AC SLD", "Array layout", "Earthing layout"], "source": src},
        {"key": "grid.special_note", "value": "terminal point is the ICOG", "source": src},
    ]
    call(s, "brief_set", items=items)
    call(s, "issue_set", kind="conflict", topic="DC/AC ratio", options=[{"value": "1.64", "source": "Annex 2"}, {"value": "1.5-1.6", "source": "cl.31"}])
    chk = data(call(s, "plan_check"))
    assert any("DC/AC ratio" in e for e in chk["errors"])
    call(s, "issue_set", id="I1", kind="conflict", topic="DC/AC ratio", options=[{"value": "1.64", "source": "Annex 2"}, {"value": "1.5-1.6", "source": "cl.31"}],
         decision="1.64", reason="the bid design")  # fmt: skip
    call(s, "issue_set", kind="gap", topic="Soil resistivity", detail="no ERT given")
    with pytest.raises(RuntimeError, match="HTTP 404"):
        call(s, "datasheet_fetch", url="https://example.com/assumed-datasheet.pdf")
    fetched = data(call(s, "datasheet_fetch", url="https://example.com/goldi.pdf"))
    assert "GS10-B144-GF-550" in fetched["text"]
    call(s, "datasheet_fetch", url="https://example.com/inv.pdf")
    call(s, "equipment_set", role="module", kind="module", manufacturer="Goldi Solar", model="GS10-B144-GF-550", datasheet="https://example.com/inv.pdf")
    assert any("does not mention model" in e for e in data(call(s, "plan_check"))["errors"])
    call(s, "equipment_set", role="module", kind="module", manufacturer="Goldi Solar", model="GS10-B144-GF-550", datasheet="https://example.com/goldi.pdf")
    call(s, "equipment_set", role="inverter", kind="inverter", manufacturer="Sungrow", model="SG1100UD-20", datasheet="https://example.com/inv.pdf")

    # site: two rectangles with stated areas, scale from areas
    feats = [
        {"name": "Block 1 (3.03 acre)", "kind": "boundary", "points": [[50, 50], [250, 50], [250, 150], [50, 150]], "stated_area_m2": 3.03 * ACRE},
        {"name": "Block 2 (0.50 acre)", "kind": "boundary", "points": [[270, 50], [350, 50], [350, 100], [270, 100]], "stated_area_m2": 0.5 * ACRE},
        {"name": "132 kV RoW", "kind": "transmission_line", "points": [[40, 30], [360, 30]], "width_m": 27},
    ]
    call(s, "site_preview", doc="site", features=feats)
    res = call(s, "site_trace", doc="site", features=feats, anchor_lat=29.4, anchor_lon=76.89)
    rep = json.loads(res.content[0].text)
    b1 = next(f for f in rep["features"] if f["name"].startswith("Block 1"))
    assert abs(b1["area_m2"] / (3.03 * ACRE) - 1) < 0.001
    gj = json.loads((root / "model" / "site.geojson").read_text())
    assert all("ASSUMED" in f["properties"]["name"] for f in gj["features"])
    assert gj["features"][2]["properties"]["width_m"] == 27

    chk = data(call(s, "plan_check"))
    assert any("landscape pages [3]" in e for e in chk["errors"])
    call(s, "source_page_image", doc="Spec", page=3, rotate=90)
    chk = data(call(s, "plan_check"))
    assert chk["ready"], chk["errors"]
    out = data(call(s, "plan_write"))
    brief = Path(out["brief"]).read_text()
    prompt = Path(out["prompt"]).read_text()
    assert "**ASSUMED:** 0" in brief and "**Decision:** 1.64" in brief
    assert "terminal point is the ICOG" in brief  # extra field kept
    assert out["template"] == "utility_central"
    assert "network_autobuild (template 'utility_central'" in prompt
    assert "Escalation" in prompt and "- AC SLD" in prompt
    assert "site.geojson" in prompt
    needs = json.loads((root / "catalog" / "needs.json").read_text())
    assert {n["role"] for n in needs} >= {"module"}


def test_bad_inputs(tmp_path):
    s = build_server(tmp_path / "p")
    with pytest.raises(RuntimeError, match="plan_create first"):
        call(s, "plan_check")
    call(s, "plan_create", name="x", segment="rooftop")
    with pytest.raises(RuntimeError, match="section.field"):
        call(s, "brief_set", items=[{"key": "nonsense", "value": 1}])
    with pytest.raises(RuntimeError, match="no file"):
        call(s, "source_add", file_path=str(tmp_path / "missing.pdf"))


def test_calc():
    v = calc.evaluate("28 * 49.9 * (1 + (-0.28/100) * (t - 25))", {"t": 0})
    assert round(v, 1) == 1495.0
    assert round(calc.evaluate("3.03 * acre")) == 12262
    with pytest.raises(ValueError):
        calc.evaluate("__import__('os')")


def test_gates(tmp_path, tender):
    s = build_server(tmp_path / "plan")
    call(s, "plan_create", name="g", segment="utility")
    call(s, "source_add", file_path=str(tender), ocr=False)
    call(s, "brief_set", items=[{"key": "site.site_file", "value": "none", "status": "not_applicable", "note": "no plan"}])
    bad = {"id": "x", "pmax": 550, "voc": None, "vmp": 107.2, "imp": 15.36, "isc": 13.77, "source": {"ref": "PVsyst"}}
    call(s, "equipment_set", role="module", kind="module", manufacturer="Goldi", model="ZZ-NOT-IN-CATALOG", datasheet="PVsyst report p.21", entry=bad)
    errs = "\n".join(data(call(s, "plan_check"))["errors"])
    assert "not_applicable is not accepted" in errs
    assert "was not fetched with datasheet_fetch" in errs
    assert "were never read" in errs
    assert "no value for voc" in errs
    assert "imp 15.36 A is not below isc" in errs
    assert "no page image viewed" in errs
