"""The packaged code-description tables and the config blocks that consume them stay in step."""

import re
from pathlib import Path

import polars as pl
import pyarrow.parquet as pq
import yaml
from meds import CodeMetadataSchema

from eICU_MEDS.code_descriptions import (
    LAB_DESCRIPTIONS_PREFIX,
    apply_static_descriptions,
    lab_fallback_description,
    read_lab_descriptions,
    read_static_code_descriptions,
    write_lab_descriptions,
)

CONFIG_FP = Path(__file__).parents[1] / "src" / "eICU_MEDS" / "configs" / "event_configs.yaml"


def _events():
    cfg = yaml.safe_load(CONFIG_FP.read_text())
    for table, body in cfg.items():
        if table in ("_defaults", "sources"):
            continue
        for name, event in body.items():
            if name == "_table":
                continue
            yield table, name, event


def _is_literal_code(code: str) -> bool:
    """A code expression with no ``$column`` reference: a bare literal or ``"X" if $col == $col``."""
    return "$" not in code.split(" if ")[0]


def test_lab_table_is_complete_and_sourced():
    df = read_lab_descriptions()
    assert not df["labname"].is_duplicated().any()
    assert df["description"].str.len_chars().min() > 0
    assert df["source"].str.len_chars().min() > 0
    # Descriptions only: the lab-name -> LOINC crosswalk is a suMEDS crosswalk table (`eicu/lab_loinc`).
    assert "loinc_code" not in df.columns


def test_static_table_covers_every_literal_code():
    static = set(read_static_code_descriptions()["code"])
    literal = set()
    for _table, _name, event in _events():
        code = str(event["code"]).strip()
        if _is_literal_code(code):
            literal.add(code.split(" if ")[0].strip().strip('"'))
    assert literal, "no literal-coded events found; the detection regex is stale"
    missing = sorted(literal - static)
    assert not missing, f"literal codes without a description: {missing}"
    extra = sorted(static - literal)
    assert not extra, f"described codes the config no longer emits: {extra}"


def test_component_coded_events_declare_a_description():
    """Every event whose code has components describes itself, through ``_self`` or the lab table."""
    undescribed = []
    for table, name, event in _events():
        code = str(event["code"])
        if _is_literal_code(code):
            continue
        meta = event.get("_metadata", {})
        blocks = [b for b in meta.values() if isinstance(b, dict) and "description" in b]
        if not blocks:
            undescribed.append(f"{table}/{name}")
    assert not undescribed, undescribed


def test_lab_blocks_use_the_packaged_prefix():
    for table, _name, event in _events():
        if table != "lab":
            continue
        block = event["_metadata"][LAB_DESCRIPTIONS_PREFIX]
        assert block["labname"] == "$labname"
        assert "$description" in block["description"]
        assert "parent_codes" not in block, "lab LOINC parents come from suMEDS (sumeds_rules.yaml)"
        for referenced in re.findall(r"\$(\w+)", " ".join(map(str, block.values()))):
            assert referenced in read_lab_descriptions().columns, referenced


def test_write_lab_descriptions(tmp_path):
    fp = write_lab_descriptions(tmp_path)
    assert fp == tmp_path / f"{LAB_DESCRIPTIONS_PREFIX}.parquet"
    labs = pl.read_parquet(fp)
    assert set(labs.schema.values()) == {pl.String}
    assert labs.height == read_lab_descriptions().height


def test_apply_static_descriptions_fills_only_nulls(tmp_path):
    codes = pl.DataFrame(
        {
            "code": ["VITALS//PERIODIC//HEARTRATE", "MEDS_BIRTH", "LAB//RESULT//Hgb//g/dL"],
            "description": [None, "already extracted", "Hemoglobin"],
            "parent_codes": pl.Series([None, None, None], dtype=pl.List(pl.String)),
            "code_template": [None, None, 'f"LAB//RESULT//{$labname}//{$labmeasurenamesystem}"'],
        }
    )
    fp = tmp_path / "codes.parquet"
    codes.write_parquet(fp)
    assert apply_static_descriptions(fp) == 1
    out = pl.read_parquet(fp)
    assert out.columns == codes.columns and out.height == codes.height
    by_code = dict(zip(out["code"], out["description"], strict=True))
    assert by_code["VITALS//PERIODIC//HEARTRATE"].startswith("Heart rate")
    assert by_code["MEDS_BIRTH"] == "already extracted"
    assert by_code["LAB//RESULT//Hgb//g/dL"] == "Hemoglobin"
    assert apply_static_descriptions(fp) == 0  # idempotent
    # The file must stay valid MEDS code metadata as read from disk (string, not large_string).
    CodeMetadataSchema.validate(pq.read_table(fp))


def test_lab_fallback_description_parses_units_and_skips_other_codes():
    codes = pl.Series(
        [
            "LAB//RESULT//total protein//g/dL",
            "LAB//SPECIMEN_DRAWN//O2 Sat (%)//%",
            "LAB//RESULT//WBC x 1000//K/mcL",
            "LAB//RESULT//UNK//UNK",
            "VITALS//PERIODIC//HEARTRATE",
            "MEDICATION//STARTED//LAB//x",
        ]
    )
    assert pl.select(lab_fallback_description(pl.lit(codes))).to_series().to_list() == [
        "Lab result (eICU lab.labName 'total protein')",
        "Lab specimen drawn (eICU lab.labName 'O2 Sat (%)')",
        "Lab result (eICU lab.labName 'WBC x 1000')",
        "Lab result (eICU lab.labName 'UNK')",
        None,
        None,
    ]


def test_unlisted_lab_codes_get_the_fallback_and_listed_ones_keep_the_table(tmp_path):
    codes = pl.DataFrame(
        {
            "code": ["LAB//RESULT//Hgb//g/dL", "LAB//RESULT//not in the table//mg/dL"],
            "description": ["Hemoglobin (eICU lab.labName 'Hgb')", None],
            "parent_codes": [None, None],
        }
    )
    fp = tmp_path / "codes.parquet"
    codes.write_parquet(fp)
    assert apply_static_descriptions(fp) == 1
    by_code = dict(pl.read_parquet(fp).select("code", "description").iter_rows())
    assert by_code["LAB//RESULT//Hgb//g/dL"] == "Hemoglobin (eICU lab.labName 'Hgb')"
    assert (
        by_code["LAB//RESULT//not in the table//mg/dL"] == "Lab result (eICU lab.labName 'not in the table')"
    )
