"""Pins the ``diagnosis.icd9code`` split shipped in ``configs/event_configs.yaml``.

Despite its name, ``icd9code`` packs **both** ICD-9-CM and ICD-10-CM codes into a single
comma-separated cell (80.4% of populated cells carry both). The config splits the cell
into positional parts and classifies each part by grammar, so the emitted code is already
vocabulary-qualified.

Two things here are easy to break silently and are therefore pinned:

* **The unroll depth.** MESSY has no list/explode, so "one event per code" is written as
  N positional extractions. N is set from the observed maximum (7) across the full 2.7M-row
  release. If a future release ever ships an 8-code cell, the 8th code is dropped with no
  error at all — so the arity is asserted structurally here, and the reasoning is recorded.
* **The classification boundary.** The ICD-9 and ICD-10 grammars overlap on V-codes, and
  the raw data contains a stray ICD-9 *procedure* code. Those must land in ``ICD_UNK``
  rather than being forced into either vocabulary.

The tests drive the config's *own* expressions rather than a re-implementation, so they
cannot drift from what ships.
"""

import polars as pl
import pytest
from MEDS_extract.config import MessyConfig

from eICU_MEDS import EVENT_CFG

# The maximum number of comma-separated codes observed in any `icd9code` cell across the
# full credentialed eICU-CRD v2.0 release (2,710,672 rows; 1,208 distinct cells). Cells
# with more parts than this would be silently truncated.
OBSERVED_MAX_CODES_PER_CELL = 7


@pytest.fixture(scope="module")
def diagnosis_table():
    cfg = MessyConfig.load(EVENT_CFG)
    return next(t for t in cfg.tables if t.input_prefix == "diagnosis")


def _apply(table, df: pl.DataFrame) -> pl.DataFrame:
    """Evaluate the table's derived columns in declaration order, as ``prepare`` does."""
    out = df.lazy()
    for name, node in table.cols.items():
        if name.startswith(("icd_", "uncoded_")):
            out = out.with_columns(node.polars_expr.alias(name))
    return out.collect()


def test_unroll_arity_is_consistent(diagnosis_table):
    """Every extracted part must have a matching classifier and a matching event."""
    cols = set(diagnosis_table.cols)
    parts = {c for c in cols if c.startswith("icd_part_")}
    codes = {c for c in cols if c.startswith("icd_code_")}
    events = {e.name for e in diagnosis_table.events if e.name.startswith("icd_")}

    assert len(parts) == OBSERVED_MAX_CODES_PER_CELL, (
        f"config unrolls {len(parts)} codes per cell but the full release contains cells with "
        f"up to {OBSERVED_MAX_CODES_PER_CELL}; a shortfall silently drops codes"
    )
    assert len(codes) == len(parts), "each icd_part_N needs an icd_code_N"
    assert len(events) == len(parts), "each icd_code_N needs an icd_N event"


def test_classification_of_representative_codes(diagnosis_table):
    """Grammar classification, including the genuinely undecidable cases.

    ``V08``/``V42.7`` are valid under *both* grammars (ICD-9 status codes whose shape is
    also a legal ICD-10 code) and ``31.1`` is an ICD-9 procedure code matching neither.
    All must be ``ICD_UNK`` — forcing them either way would assert something the cell does
    not actually say.
    """
    cases = {
        "401.9, I10": ["ICD9CM/401.9", "ICD10CM/I10"],
        "038.9, 518.81, R65.20, J96.0": [
            "ICD9CM/038.9",
            "ICD9CM/518.81",
            "ICD10CM/R65.20",
            "ICD10CM/J96.0",
        ],
        "S32.00": ["ICD10CM/S32.00"],
        "E870": ["ICD9CM/E870"],  # ICD-9 E-code: 4 chars, so not a legal ICD-10 code
        "V42.7": ["ICD_UNK/V42.7"],  # ambiguous: matches both grammars
        "31.1": ["ICD_UNK/31.1"],  # ICD-9 procedure code: matches neither
    }
    df = pl.DataFrame({"icd9code": list(cases), "diagnosisstring": ["a|b|c"] * len(cases)})
    got = _apply(diagnosis_table, df)

    n = OBSERVED_MAX_CODES_PER_CELL
    for i, (cell, expected) in enumerate(cases.items()):
        emitted = [got[f"icd_code_{j}"][i] for j in range(1, n + 1)]
        assert [c for c in emitted if c is not None] == expected, f"for cell {cell!r}"


def test_every_comma_part_is_emitted_exactly_once(diagnosis_table):
    """No part may be dropped or duplicated, at any arity up to the unroll depth."""
    cells = ["401.9", "401.9, I10", "1.1, 2.2, 3.3, 4.4, 5.5, 6.6, 7.7"]
    df = pl.DataFrame({"icd9code": cells, "diagnosisstring": ["a|b|c"] * len(cells)})
    got = _apply(diagnosis_table, df)

    n = OBSERVED_MAX_CODES_PER_CELL
    for i, cell in enumerate(cells):
        emitted = [got[f"icd_code_{j}"][i] for j in range(1, n + 1) if got[f"icd_code_{j}"][i]]
        assert len(emitted) == len(cell.split(", ")), f"arity mismatch for {cell!r}"


def test_uncoded_rows_become_unk_dx(diagnosis_table):
    """Rows with no ICD code at all keep their clinical content under ``UNK_DX``.

    15.9% of the full release is uncoded (``icd9code`` is the empty string, never null).
    Those rows still carry a ``diagnosisstring``, and dropping them would discard real
    diagnoses purely because eICU never assigned a billing code. ``UNK_DX`` (no vocabulary)
    is deliberately distinct from ``ICD_UNK`` (an ICD code of undecidable vocabulary).
    """
    df = pl.DataFrame(
        {
            "icd9code": ["", "401.9, I10"],
            "diagnosisstring": ["burns/trauma|burns|burn of arm", "cardiovascular|htn"],
        }
    )
    got = _apply(diagnosis_table, df)

    assert got["uncoded_dx"][0] == "UNK_DX//burns/trauma|burns|burn of arm"
    assert got["uncoded_dx"][1] is None, "coded rows must not also emit an UNK_DX event"
    assert got["icd_code_1"][0] is None, "uncoded rows must not emit an ICD event"
