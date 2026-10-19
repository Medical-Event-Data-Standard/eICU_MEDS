import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory

import polars as pl


def test_e2e():
    with TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)

        command_parts = [
            "MEDS_extract-eICU",
            f"root_output_dir={root.resolve()!s}",
            "do_download=True",
            "do_overwrite=True",
            "do_demo=True",
        ]

        full_cmd = " ".join(command_parts)
        command_out = subprocess.run(full_cmd, shell=True, capture_output=True)

        stderr = command_out.stderr.decode()
        stdout = command_out.stdout.decode()

        err_message = (
            f"Command failed with return code {command_out.returncode}.\n"
            f"Command stdout:\n{stdout}\n"
            f"Command stderr:\n{stderr}"
        )
        assert command_out.returncode == 0, err_message

        _validate_meds_dataset(root / "MEDS_output")


def _validate_meds_dataset(dataset_path: Path):
    data_path = dataset_path / "data"
    metadata_path = dataset_path / "metadata"

    data_files = list(data_path.glob("**/*.parquet"))
    all_data = [x for x in data_path.glob("**/*") if x.is_file()]
    assert len(data_files) > 0, f"No data files found in {data_path}; found {all_data}"

    all_meta_files = [x for x in metadata_path.glob("**/*") if x.is_file()]

    expected_files = {
        "dataset.json",
        "codes.parquet",
        "subject_splits.parquet",
    }

    for fname in expected_files:
        fpath = metadata_path / fname
        assert fpath.exists(), f"{fname} not found in {metadata_path}; found {all_meta_files}"

    _validate_data_correctness(data_path, metadata_path)


# Codes whose events exist only to carry a measurement; an event without a value is the
# null-guard regression (wide vitals rows, or empty weight/height cells, emitting anyway).
VALUE_ONLY_CODES = (
    "ICU_ADMISSION_WEIGHT",
    "ICU_ADMISSION_HEIGHT",
    "ICU_DISCHARGE_WEIGHT",
    "SUBJECT_WEIGHT_AT_INFUSION",
)


def _validate_data_correctness(data_path: Path, metadata_path: Path):
    """Pins the data-correctness fixes on the demo output, so a regression fails CI."""
    df = pl.scan_parquet(data_path / "**/*.parquet")

    valueless = (
        df.filter(pl.col("code").str.starts_with("VITALS//") | pl.col("code").is_in(VALUE_ONLY_CODES))
        .filter(pl.col("numeric_value").is_null())
        .group_by("code")
        .len()
        .collect()
    )
    assert valueless.is_empty(), f"Events emitted without a value:\n{valueless}"

    births = (
        df.filter(pl.col("code") == "MEDS_BIRTH")
        .group_by("subject_id")
        .agg(pl.len().alias("n_births"), pl.col("time").min().alias("birth"))
    )
    max_births = births.select(pl.col("n_births").max()).collect().item()
    assert max_births == 1, f"Subjects carry up to {max_births} MEDS_BIRTH events; expected exactly 1."

    first_admit = (
        df.filter(pl.col("code").str.starts_with("HOSPITAL_ADMISSION//"))
        .group_by("subject_id")
        .agg(pl.col("time").min().alias("admit"))
    )
    n_born_after_admit = (
        (births.join(first_admit, on="subject_id").filter(pl.col("birth") > pl.col("admit")).select(pl.len()))
        .collect()
        .item()
    )
    assert n_born_after_admit == 0, f"{n_born_after_admit} subjects are born after their own admission."

    codes = pl.read_parquet(metadata_path / "codes.parquet")
    n_with_parents = codes.filter(pl.col("parent_codes").list.len() > 0).height
    assert n_with_parents > 0, "No code in codes.parquet carries parent_codes; the dx -> ICD link is gone."
