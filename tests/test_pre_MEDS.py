"""Tests for the tables the ``eicu_pre_meds`` pre-stage writes, and for ``MEDS_extract-eICU``'s input dir.

The ``dx_icd_map`` build itself is covered in ``test_dx_parent_codes.py``; these tests pin what
surrounds it: the stage writes only its own tables and never touches the raw directory, the demo's
lowercase ``infusiondrug`` spelling, and stale outputs from the pre-0.7 extractor.
"""

import gzip

import polars as pl
import pytest

from eICU_MEDS.pre_MEDS import assemble_input_dir, stage_tables

DIAGNOSIS_CSV = 'diagnosisstring,icd9code\np|q,"486, J18.9"\n'


def _write_raw(raw_dir, *names):
    raw_dir.mkdir()
    (raw_dir / "diagnosis.csv.gz").write_bytes(gzip.compress(DIAGNOSIS_CSV.encode()))
    for name in names:
        (raw_dir / name).write_bytes(gzip.compress(b"a\n1\n"))


def _tree(directory):
    return {fp.name: fp.read_bytes() for fp in directory.iterdir()}


def test_stage_writes_only_its_own_tables(tmp_path):
    _write_raw(tmp_path / "raw", "patient.csv.gz", "infusionDrug.csv.gz")
    before = _tree(tmp_path / "raw")
    stage_tables(tmp_path / "raw", tmp_path / "out")

    assert {fp.name for fp in (tmp_path / "out").iterdir()} == {
        "dx_icd_map.parquet",
        "lab_descriptions.parquet",
    }
    assert _tree(tmp_path / "raw") == before
    dx_map = pl.read_parquet(tmp_path / "out" / "dx_icd_map.parquet")
    assert dx_map.rows() == [("p|q", "486"), ("p|q", "J18.9")]


def test_demo_infusiondrug_is_staged_under_the_release_spelling(tmp_path):
    _write_raw(tmp_path / "raw", "infusiondrug.csv.gz")
    stage_tables(tmp_path / "raw", tmp_path / "out")

    names = {fp.name for fp in (tmp_path / "out").iterdir()}
    assert "infusionDrug.csv.gz" in names
    assert "infusiondrug.csv.gz" not in names
    assert (tmp_path / "out" / "infusionDrug.csv.gz").resolve() == (
        tmp_path / "raw" / "infusiondrug.csv.gz"
    ).resolve()


def test_rerun_gives_the_same_tables(tmp_path):
    _write_raw(tmp_path / "raw", "infusiondrug.csv.gz")
    stage_tables(tmp_path / "raw", tmp_path / "out")
    first = _tree(tmp_path / "out")
    stage_tables(tmp_path / "raw", tmp_path / "out")
    assert _tree(tmp_path / "out") == first


def test_missing_diagnosis_table_fails(tmp_path):
    (tmp_path / "raw").mkdir()
    with pytest.raises(FileNotFoundError, match="diagnosis"):
        stage_tables(tmp_path / "raw", tmp_path / "out")


def test_assembled_input_dir_links_the_demo_infusiondrug_under_the_release_spelling(tmp_path):
    _write_raw(tmp_path / "raw", "infusiondrug.csv.gz")
    assemble_input_dir(tmp_path / "raw", tmp_path / "out")

    names = {fp.name for fp in (tmp_path / "out").iterdir()}
    assert "infusionDrug.csv.gz" in names
    assert "infusiondrug.csv.gz" not in names
    assert "diagnosis.csv.gz" in names


def test_stale_pre_07_tables_stop_the_run(tmp_path):
    _write_raw(tmp_path / "raw")
    (tmp_path / "out").mkdir()
    (tmp_path / "out" / "lab.parquet").touch()

    with pytest.raises(FileExistsError, match=r"lab\.parquet"):
        assemble_input_dir(tmp_path / "raw", tmp_path / "out")


def test_rerun_over_own_output_is_allowed(tmp_path):
    _write_raw(tmp_path / "raw")
    assemble_input_dir(tmp_path / "raw", tmp_path / "out")
    assemble_input_dir(tmp_path / "raw", tmp_path / "out")
