"""Every code the config emits is described in the config, and the lab table feeds the lab events.

The expressions are read from the shipped ``configs/event_configs.yaml`` and evaluated with dftly, so
the pinned text cannot drift from what ships.
"""

import re

import polars as pl
import pytest
from dftly import Parser
from MEDS_extract.config import MessyConfig

from eICU_MEDS import EVENT_CFG
from eICU_MEDS.pre_MEDS import LAB_DESCRIPTIONS_PREFIX, read_lab_descriptions, write_lab_descriptions


@pytest.fixture(scope="module")
def events() -> dict[str, object]:
    """``{"<table>/<event>": EventConfig}`` for every event in the shipped config."""
    cfg = MessyConfig.load(EVENT_CFG)
    return {f"{t.input_prefix}/{e.name}": e for t in cfg.event_tables for e in t.events}


def _evaluate(expr: str, frame: pl.DataFrame) -> list:
    return frame.select(v=Parser.to_polars({"v": expr})["v"])["v"].to_list()


def test_every_event_describes_its_code_itself(events):
    """A ``_self`` description on every event: f-string templates, constants on literal codes."""
    undescribed = sorted(
        name for name, event in events.items() if "description" not in event.metadata["_self"]
    )
    assert not undescribed, undescribed


def test_literal_codes_carry_a_constant_description_with_its_source(events):
    literal = {name: event for name, event in events.items() if not event.code_source_columns}
    literal |= {
        name: event
        for name, event in events.items()
        if re.fullmatch(r'"[A-Z0-9_/]+" if \$(\w+) == \$\1', str(event.raw_code))
    }
    assert len(literal) == 34
    for name, event in literal.items():
        description = event.metadata["_self"]["description"]
        assert re.fullmatch(r'"[^"$\\{}]+"', description), (name, description)
        # dftly reads the constant back verbatim, so the YAML folding added nothing.
        assert _evaluate(description, pl.DataFrame({"x": [1]})) == [description[1:-1]]
    # Each constant records where its text comes from.
    assert EVENT_CFG.read_text().count("    # Source: ") == len(literal)


def test_lab_events_join_the_packaged_table():
    """The ``lab`` table joins ``lab_descriptions`` on the raw lab name and reads only its description."""
    cfg = MessyConfig.load(EVENT_CFG)
    (lab,) = [t for t in cfg.event_tables if t.input_prefix == "lab"]
    joins = {j.input_prefix: j for j in lab.joins}
    join = joins[LAB_DESCRIPTIONS_PREFIX]
    assert (join.left_on, join.right_on, join.cols) == (("labname",), ("labname",), ("description",))
    assert set(join.cols) <= set(read_lab_descriptions().columns)


@pytest.mark.parametrize(
    ("event", "listed", "unlisted", "blank"),
    [
        (
            "lab/lab_result",
            "Hemoglobin (eICU lab.labName 'Hgb')",
            "Lab result (eICU lab.labName 'new lab')",
            "Lab result (eICU lab.labName 'UNK')",
        ),
        (
            "lab/lab_specimen_drawn",
            "Lab specimen drawn: Hemoglobin (eICU lab.labName 'Hgb')",
            "Lab specimen drawn (eICU lab.labName 'new lab')",
            "Lab specimen drawn (eICU lab.labName 'UNK')",
        ),
    ],
)
def test_lab_descriptions_fall_back_for_a_name_the_table_does_not_list(
    events, event, listed, unlisted, blank
):
    """The joined description wins; a lab name outside the table (or a blank one) gets a generic one.

    The generic text is the one releases up to 0.2.0 built from the code after the pipeline.
    """
    frame = pl.DataFrame(
        {"labname": ["Hgb", "new lab", None], "description": ["Hemoglobin", None, None]},
        schema={"labname": pl.String, "description": pl.String},
    )
    assert _evaluate(events[event].metadata["_self"]["description"], frame) == [listed, unlisted, blank]


def test_lab_table_is_complete_and_sourced():
    df = read_lab_descriptions()
    assert not df["labname"].is_duplicated().any()
    assert df["description"].str.len_chars().min() > 0
    assert df["source"].str.len_chars().min() > 0
    # Descriptions only: the lab-name -> LOINC crosswalk is a suMEDS crosswalk table (`eicu/lab_loinc`).
    assert "loinc_code" not in df.columns


def test_write_lab_descriptions(tmp_path):
    fp = write_lab_descriptions(tmp_path)
    assert fp == tmp_path / f"{LAB_DESCRIPTIONS_PREFIX}.parquet"
    labs = pl.read_parquet(fp)
    assert set(labs.schema.values()) == {pl.String}
    assert labs.height == read_lab_descriptions().height
