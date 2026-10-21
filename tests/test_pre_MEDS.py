"""Tests for how ``pre_MEDS.main`` assembles its input directory.

The ``dx_icd_map`` build itself is covered in ``test_dx_parent_codes.py``; these tests
pin the pass-through around it: the demo's lowercase ``infusiondrug`` spelling and stale
outputs from the pre-0.7 extractor.
"""

import gzip

import pytest

from eICU_MEDS.pre_MEDS import main as pre_MEDS_main

DIAGNOSIS_CSV = 'diagnosisstring,icd9code\np|q,"486, J18.9"\n'


def _write_raw(raw_dir, *names):
    raw_dir.mkdir()
    (raw_dir / "diagnosis.csv.gz").write_bytes(gzip.compress(DIAGNOSIS_CSV.encode()))
    for name in names:
        (raw_dir / name).write_bytes(gzip.compress(b"a\n1\n"))


def test_demo_infusiondrug_is_linked_under_the_release_spelling(tmp_path):
    _write_raw(tmp_path / "raw", "infusiondrug.csv.gz")
    pre_MEDS_main(tmp_path / "raw", tmp_path / "out")

    names = {fp.name for fp in (tmp_path / "out").iterdir()}
    assert "infusionDrug.csv.gz" in names
    assert "infusiondrug.csv.gz" not in names


def test_stale_pre_07_tables_stop_the_run(tmp_path):
    _write_raw(tmp_path / "raw")
    (tmp_path / "out").mkdir()
    (tmp_path / "out" / "lab.parquet").touch()

    with pytest.raises(FileExistsError, match=r"lab\.parquet"):
        pre_MEDS_main(tmp_path / "raw", tmp_path / "out")


def test_rerun_over_own_output_is_allowed(tmp_path):
    _write_raw(tmp_path / "raw")
    pre_MEDS_main(tmp_path / "raw", tmp_path / "out")
    pre_MEDS_main(tmp_path / "raw", tmp_path / "out", do_overwrite=True)
