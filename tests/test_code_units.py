"""Each code's unit is declared in the spec, so MEDS-Extract writes it to ``metadata/codes.parquet``.

Lab codes take the unit from their own unit component and intake/output codes from the unit label in
their path, through ``_self`` templates; the literal codes whose unit the eICU-CRD documentation
states carry it as a ``_self`` constant, as they carry their description. The expressions are read
from the shipped ``configs/event_configs.yaml`` and evaluated with dftly; ``test_run_with_enrichment``
checks the units a real run writes.
"""

import re

import polars as pl
import pytest
from dftly import Parser
from MEDS_extract.config import MessyConfig

from eICU_MEDS import EVENT_CFG

# The literal codes whose column the eICU-CRD table documentation gives a unit for, and that unit.
DOCUMENTED_UNITS = {
    "patient/unit_admission_weight": "kilograms",
    "patient/unit_admission_height": "cm",
    "patient/unit_discharge_weight": "kilograms",
    "infusionDrug/patient_weight": "kilograms",
    "vitalPeriodic/temperature": "celsius",
}


@pytest.fixture(scope="module")
def events() -> dict[str, object]:
    """``{"<table>/<event>": EventConfig}`` for every event in the shipped config."""
    cfg = MessyConfig.load(EVENT_CFG)
    return {f"{t.input_prefix}/{e.name}": e for t in cfg.event_tables for e in t.events}


def _unit_of(event, columns: dict) -> list:
    expr = Parser.to_polars({"u": event.metadata["_self"]["unit"]})["u"]
    return pl.DataFrame(columns, schema=dict.fromkeys(columns, pl.String)).select(u=expr)["u"].to_list()


def test_lab_result_unit_is_its_own_unit_component(events):
    cells = ["mg/dL", "%", "", " ", None]
    # As recorded; a blank or null cell is no unit.
    assert _unit_of(events["lab/lab_result"], {"labmeasurenamesystem": cells}) == [
        "mg/dL",
        "%",
        None,
        None,
        None,
    ]


def test_io_unit_is_the_last_parenthesized_label_when_it_is_a_unit(events):
    paths = {
        # A heading's label applies to the cells under it.
        "flowsheet|Flowsheet Cell Labels|I&O|Output (ml)|Urine": "ml",
        "flowsheet|Flowsheet Cell Labels|I&O|Intake (ml)|Generic Intake (ml)|P.O.": "ml",
        # A leaf's own label wins over the heading's, as written.
        "flowsheet|Flowsheet Cell Labels|I&O|Weight|Bodyweight (kg)": "kg",
        "flowsheet|Flowsheet Cell Labels|I&O|Weight|Bodyweight (lb)": "lb",
        "flowsheet|Flowsheet Cell Labels|I&O|Intake (ml)|Generic Intake (ml)|Volume (mL)-NS infusion": "mL",
        "flowsheet|Flowsheet Cell Labels|I&O|Intake (ml)|Generic Intake (ml)|Total Amount (mcg)-PCA": "mcg",
        # Any other last parenthesized token is not a unit and leaves the unit empty.
        "flowsheet|Flowsheet Cell Labels|I&O|Output (ml)|Gastric (NG)": None,
        "flowsheet|Flowsheet Cell Labels|I&O|Intake (ml)|Generic Intake (ml)|Other (volume)": None,
        # A path without a unit label has no unit.
        "flowsheet|Flowsheet Cell Labels|I&O|Weight|Bodyweight": None,
    }
    got = _unit_of(events["intakeOutput/intake_output"], {"cellpath": list(paths)})
    assert got == list(paths.values())


def test_documented_units_are_constants_on_their_literal_codes(events):
    with_unit = {name for name, event in events.items() if "unit" in event.metadata["_self"]}
    literal = with_unit - {"lab/lab_result", "intakeOutput/intake_output"}
    assert literal == set(DOCUMENTED_UNITS)
    for name, unit in DOCUMENTED_UNITS.items():
        event = events[name]
        assert not event.code_source_columns or re.fullmatch(
            r'"[A-Z_/]+" if \$(\w+) == \$\1', str(event.raw_code)
        )
        assert event.metadata["_self"]["unit"] == f'"{unit}"', name
        # The description says the same unit.
        assert unit.lower() in event.metadata["_self"]["description"].lower(), name


def test_documented_units_cite_the_documentation():
    """Each constant unit is preceded by the documentation's sentence that states it, and the page."""
    text = EVENT_CFG.read_text()
    blocks = re.findall(r"((?:        # .*\n)+)        unit: '\"(\w+)\"'\n", text)
    assert len(blocks) == len(DOCUMENTED_UNITS)
    for comment, unit in blocks:
        comment = " ".join(line.strip().removeprefix("# ") for line in comment.splitlines())
        assert "https://eicu.mit.edu/eicutables/" in comment, comment
        assert re.search(rf"'[^']*\b{unit}'", comment), comment
