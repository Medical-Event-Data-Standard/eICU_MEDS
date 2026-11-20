"""A lab code's unit component is ``lab.labMeasureNameSystem`` stripped, and `UNK` when missing or blank.

MEDS-Extract compares units stripped and drops blank ones when it writes ``codes.parquet``, so a
code that kept the whitespace would split one unit over several codes (`` mg/dL``, ``mg/dL``), and a
blank one would give a code ending in whitespace (or in ``//``) with no unit. The expressions are
evaluated straight from the shipped config with dftly, as MEDS-Extract evaluates them.
"""

import polars as pl
import pytest
import yaml
from dftly import Parser

from eICU_MEDS import EVENT_CFG

UNITS = ["mg/dL", " mg/dL", "mg/dL ", "\tmg/dL\n", "K/mcL", "  ", "", None]
EXPECTED = ["mg/dL", "mg/dL", "mg/dL", "mg/dL", "K/mcL", "UNK", "UNK", "UNK"]


@pytest.mark.parametrize(
    "event,prefix", [("lab_result", "LAB//RESULT//"), ("lab_specimen_drawn", "LAB//SPECIMEN_DRAWN//")]
)
def test_lab_unit_component_is_stripped_and_blank_is_unk(event, prefix):
    block = yaml.safe_load(EVENT_CFG.read_text())["lab"]
    raw = pl.DataFrame(
        {"labmeasurenamesystem": UNITS, "labname": ["Hgb"] * len(UNITS)},
        schema={"labmeasurenamesystem": pl.String, "labname": pl.String},
    )
    unit = {"_value_unit": block["_table"]["cols"]["_value_unit"]}
    with_cols = raw.with_columns(**Parser.to_polars(unit))
    codes = with_cols.select(**Parser.to_polars({"code": block[event]["code"]}))["code"].to_list()
    assert codes == [f"{prefix}Hgb//{u}" for u in EXPECTED]


def test_lab_unit_metadata_stays_the_raw_column():
    """MEDS-Extract strips ``unit`` itself when it writes ``codes.parquet``."""
    block = yaml.safe_load(EVENT_CFG.read_text())["lab"]
    assert block["lab_result"]["_metadata"]["_self"]["unit"] == (
        "$labmeasurenamesystem if (/\\S/ in $labmeasurenamesystem)"
    )
