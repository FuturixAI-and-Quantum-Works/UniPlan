"""Drafted entries are checked with UniCAD's own catalog_add check, so a plan never hands the build an entry it cannot add."""

import pytest

store = pytest.importorskip("unicad.catalog.store")
if not hasattr(store, "validate_entry"):
    pytest.skip("this UniCAD has no catalog_add", allow_module_level=True)

from uniplan import catalog  # noqa: E402


def _first(kind: str) -> dict:
    entry = catalog.example_entry(kind)
    if entry is None:
        pytest.skip("UniCAD's catalog files were not found")
    return dict(entry)


def test_a_shipped_entry_passes():
    assert catalog.validate(_first("module")) == []
    assert catalog.validate(_first("inverter")) == []


def test_the_shapes_a_drafter_gets_wrong_are_named():
    module = dict(_first("module"), id="goldi-gs10-b144-gf", tech="perc")
    problems = catalog.validate(module)
    assert problems and "tech" in problems[0]

    inverter = dict(_first("inverter"), id="sungrow-sg1100ud-20", max_efficiency=99, dimensions=[700, 2290, 1525])
    [problem] = catalog.validate(inverter)
    assert "max_efficiency" in problem and "dimensions" in problem
