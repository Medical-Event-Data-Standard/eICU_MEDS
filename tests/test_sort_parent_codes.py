"""The ``eicu_sort_parent_codes`` post-stage sorts the ETL's parents and leaves an enriched file alone."""

import polars as pl
import pyarrow as pa
import pyarrow.parquet as pq
from meds import CodeMetadataSchema

from eICU_MEDS.sort_parent_codes import sort_parent_codes


def test_parent_codes_are_sorted_whatever_their_order(tmp_path):
    """The written file does not depend on the order MEDS-Extract listed the parents in."""
    fp = tmp_path / "codes.parquet"
    for parents in (["ICD9CM/486", "ICD10CM/J18.9"], ["ICD10CM/J18.9", "ICD9CM/486"]):
        pq.write_table(
            pa.table({"code": ["DIAGNOSIS//x"], "description": ["x"], "parent_codes": [parents]}), fp
        )
        assert sort_parent_codes(fp)
        assert pl.read_parquet(fp)["parent_codes"].to_list() == [["ICD10CM/J18.9", "ICD9CM/486"]]
        CodeMetadataSchema.validate(pq.read_table(fp))


def test_the_schema_meds_extract_wrote_is_kept(tmp_path):
    """Polars writes strings as ``large_string``; the stage writes back the types it read."""
    fp = tmp_path / "codes.parquet"
    schema = pa.schema(
        [
            ("code", pa.string()),
            ("description", pa.string()),
            ("parent_codes", pa.list_(pa.string())),
            ("code_template", pa.large_string()),
        ]
    )
    table = pa.table(
        {
            "code": ["DIAGNOSIS//x", "MEDS_BIRTH"],
            "description": ["x", None],
            "parent_codes": [["ICD9CM/486", "ICD10CM/J18.9"], None],
            "code_template": ['f"DIAGNOSIS//{$diagnosisstring}"', None],
        },
        schema=schema,
    )
    pq.write_table(table, fp)
    assert sort_parent_codes(fp)
    out = pq.read_table(fp)
    assert out.schema == schema
    assert out["code_template"].to_pylist() == table["code_template"].to_pylist()
    assert sort_parent_codes(fp)
    assert pq.read_table(fp).equals(out)  # idempotent


def test_enriched_parent_order_is_kept(tmp_path):
    """Running the stage again over a suMEDS-enriched file keeps the ETL's parents ahead of suMEDS's.

    suMEDS 0.6 writes ``parent_names`` as a ``map<string, string>`` from parent to name (a same-table
    parent has no name).
    """
    fp = tmp_path / "codes.parquet"
    names = pa.array([[("ICD9CM/486", "Pneumonia, organism unspecified")]], pa.map_(pa.string(), pa.string()))
    pq.write_table(
        pa.table(
            {
                "code": ["DIAGNOSIS//x|y"],
                "description": ["x|y"],
                "parent_codes": [["ICD9CM/486", "DIAGNOSIS//x"]],
                "parent_names": names,
            }
        ),
        fp,
    )
    before = fp.read_bytes()
    assert not sort_parent_codes(fp)
    assert fp.read_bytes() == before
