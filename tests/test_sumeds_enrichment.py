"""The opt-in suMEDS step (``do_enrich=True``) enriches ``codes.parquet`` in place and stays valid MEDS.

The packaged rules themselves are checked in ``test_sumeds_rules.py``. These tests run offline with one
hierarchy rule and an empty local pack index, so that suMEDS fetches nothing.
"""

import json
import sys

import polars as pl
import pyarrow.parquet as pq
import pytest
from meds import CodeMetadataSchema
from sumeds import EnrichmentConfig
from sumeds.ontology import INDEX_SCHEMA

from eICU_MEDS.sumeds_enrichment import enrich_code_metadata, sumeds_config

RULES = """
schema: sumeds-rules/0.1
hierarchy:
  - id: dx
    pattern: "^(?P<prefix>DIAGNOSIS//)(?P<path>.+)$"
    group: path
    separator: "|"
"""


@pytest.fixture
def offline_config(tmp_path):
    packs = tmp_path / "packs"
    packs.mkdir()
    (packs / "index.json").write_text(json.dumps({"schema": INDEX_SCHEMA, "vocabularies": {}}))
    rules = tmp_path / "rules.yaml"
    rules.write_text(RULES)
    return EnrichmentConfig(rules=rules, ontology_source=str(packs), offline=True)


def test_enrichment_is_in_place_and_keeps_the_etl_metadata(tmp_path, offline_config):
    codes = pl.DataFrame(
        {
            "code": ["DIAGNOSIS//a", "DIAGNOSIS//a|b", "VITALS//PERIODIC//HEARTRATE"],
            "description": ["dx a", "dx a|b", "Heart rate"],
            "parent_codes": [None, ["ICD9CM/486", "ICD10CM/J18.9"], None],
            "code_template": ['f"DIAGNOSIS//{$diagnosisstring}"'] * 2 + [None],
        }
    )
    fp = tmp_path / "codes.parquet"
    codes.write_parquet(fp)

    assert enrich_code_metadata(fp, offline_config) == (2, 3)

    out = pl.read_parquet(fp).sort("code")
    assert out.select(codes.columns).drop("parent_codes").equals(codes.drop("parent_codes"))
    assert out["parent_codes"].to_list() == [None, ["DIAGNOSIS//a", "ICD10CM/J18.9", "ICD9CM/486"], None]
    CodeMetadataSchema.validate(pq.read_table(fp))

    assert enrich_code_metadata(fp, offline_config) == (3, 3)  # idempotent


def test_missing_sumeds_fails_with_the_install_hint(monkeypatch):
    """``do_enrich=True`` builds this config before the extraction, so the hint comes up front."""
    monkeypatch.setitem(sys.modules, "sumeds", None)
    with pytest.raises(ImportError, match=r"eICU-MEDS\[sumeds\]"):
        sumeds_config()


def test_packaged_rules_load():
    assert {rule.id for rule in sumeds_config().rules.hierarchy} == {
        "eicu-diagnosis-paths",
        "eicu-admission-diagnosis-paths",
        "eicu-treatment-paths",
    }
