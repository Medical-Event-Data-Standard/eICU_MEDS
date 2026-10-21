"""Pins the diagnosis ``parent_codes`` mapping: pre-MEDS explode + in-config classification.

The work is split deliberately. ``pre_MEDS.build_dx_icd_map`` does only the explode — one
row per ``(diagnosisstring, icd_code)`` — because dftly has no list type and so cannot turn
a comma-joined cell into several metadata rows (mmcdermott/dftly#87). Everything else, the
vocabulary decision and the key format, lives in ``configs/event_configs.yaml``.

These tests drive the config's *own* ``parent_codes`` expression rather than restating the
grammars, so the pinned behavior cannot drift from what ships.
"""

import polars as pl
import pytest
from dftly import Parser
from MEDS_extract.config import MessyConfig

from eICU_MEDS import EVENT_CFG
from eICU_MEDS.pre_MEDS import build_dx_icd_map


@pytest.fixture(scope="module")
def parent_codes_expr():
    """The shipped ``parent_codes`` expression from the diagnosis event's metadata block."""
    cfg = MessyConfig.load(EVENT_CFG)
    table = next(t for t in cfg.event_tables if t.input_prefix == "diagnosis")
    (event,) = table.events
    return Parser.to_polars({"v": event.metadata["dx_icd_map"]["parent_codes"]})["v"]


def _classify(expr, codes: list[str]) -> list[str | None]:
    return pl.DataFrame({"icd_code": codes}).select(v=expr)["v"].to_list()


def test_vocabulary_is_decided_by_grammar(parent_codes_expr):
    """Unambiguous codes get a MIMIC-format ``<VOCAB>/<code>`` parent.

    eICU codes already carry their dots, so unlike MIMIC no normalization is applied —
    only the vocabulary prefix.
    """
    cases = {
        "401.9": "ICD9CM/401.9",
        "430": "ICD9CM/430",
        "E870": "ICD9CM/E870",  # ICD-9 E-code: 4 chars, so not a legal ICD-10 code
        "I10": "ICD10CM/I10",
        "E11.9": "ICD10CM/E11.9",  # letter-initial but unambiguously ICD-10
        "S32.00": "ICD10CM/S32.00",
    }
    assert _classify(parent_codes_expr, list(cases)) == list(cases.values())


def test_letter_initial_icd9_is_not_misread_as_icd10(parent_codes_expr):
    """Guards against the (incorrect) advice in MIT-LCP/eicu-code#176.

    A maintainer there suggests "ICD9 does not include any letters, so some basic regex
    should be able to select ICD9 or ICD10". ICD-9 E-codes and V-codes are letter-initial,
    so a letter check misclassifies them — and misclassifies ICD-10 E-codes in the other
    direction.
    """
    assert _classify(parent_codes_expr, ["E870", "E980.0"]) == ["ICD9CM/E870", "ICD9CM/E980.0"]
    assert _classify(parent_codes_expr, ["E11.9", "E87.2"]) == ["ICD10CM/E11.9", "ICD10CM/E87.2"]


def test_ambiguous_and_unmatched_codes_get_no_parent(parent_codes_expr):
    """Only unambiguous codes become parents; the rest yield null rather than a guess.

    The grammars overlap on V-codes (legal under both), and ``31.1`` is an ICD-9 *procedure*
    code that leaked into a diagnosis column and matches neither. Emitting either as a
    parent would assert a vocabulary the data does not actually name. Nulls are dropped by
    the ``parent_codes`` union, so these simply contribute nothing.
    """
    ambiguous = ["V08", "V42.7", "V42.83", "V62.84"]
    unmatched = ["31.1", "", "not-a-code"]
    assert _classify(parent_codes_expr, ambiguous + unmatched) == [None] * 7


def test_explode_feeds_the_classifier_end_to_end(parent_codes_expr):
    """The two halves compose: pre-MEDS explodes, the config classifies."""
    raw = pl.LazyFrame(
        {
            "diagnosisstring": ["cv|htn", "resp|sepsis+arf", "onc|leukemia"],
            "icd9code": ["401.9, I10", "038.9, 518.81, R65.20, J96.0", ""],
        }
    )
    mapped = build_dx_icd_map(raw).collect()
    parents = (
        mapped.with_columns(parent=parent_codes_expr)
        .drop_nulls("parent")
        .group_by("diagnosisstring")
        .agg(pl.col("parent").sort())
    )
    got = dict(parents.iter_rows())

    assert got["cv|htn"] == ["ICD10CM/I10", "ICD9CM/401.9"]
    assert got["resp|sepsis+arf"] == [
        "ICD10CM/J96.0",
        "ICD10CM/R65.20",
        "ICD9CM/038.9",
        "ICD9CM/518.81",
    ]
    assert "onc|leukemia" not in got, "an uncoded problem must contribute no parents"


def test_duplicate_codes_collapse(parent_codes_expr):
    """Vendor artifacts like ``486, 486, 486, 486, 486, J18.9`` must not repeat a parent."""
    raw = pl.LazyFrame({"diagnosisstring": ["p|q"], "icd9code": ["486, 486, 486, 486, 486, J18.9"]})
    mapped = build_dx_icd_map(raw).collect()
    parents = sorted(mapped.with_columns(p=parent_codes_expr).drop_nulls("p")["p"].to_list())
    assert parents == ["ICD10CM/J18.9", "ICD9CM/486"]
