import json
import os
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory

import polars as pl
import pyarrow.parquet as pq
from meds import CodeMetadataSchema, DatasetMetadataSchema


def test_e2e():
    with TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)

        # The run ends with the spec's `sumeds_enrich` stage, which checks the packaged rules against
        # suMEDS's real ontology data (fetched from GitHub); test_run_with_enrichment.py stubs that
        # data out. `strict` fails the run if that fetch fails, which a user's run would only record,
        # so that CI retries it rather than passing unenriched.
        command = [
            "meds-extract-run",
            "spec=eICU",
            f"output_dir={root.resolve()!s}",
            "dataset_key=demo",
            "download_concurrency=8",
            "overrides=['++stage_cfg.strict=true']",
        ]

        # Point the download at a persistent directory (e.g. one restored by CI's
        # actions/cache) so the demo is not refetched on every run. Files already
        # there are verified against PhysioNet's SHA256SUMS and skipped.
        cached_raw_dir = os.environ.get("EICU_MEDS_DEMO_RAW_DIR")
        if cached_raw_dir:
            command.append(f"download_dest_dir={Path(cached_raw_dir).expanduser().resolve()!s}")

        command_out = subprocess.run(command, capture_output=True)

        stderr = command_out.stderr.decode()
        stdout = command_out.stdout.decode()

        err_message = (
            f"Command failed with return code {command_out.returncode}.\n"
            f"Command stdout:\n{stdout}\n"
            f"Command stderr:\n{stderr}"
        )
        assert command_out.returncode == 0, err_message

        _validate_meds_dataset(root)


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

    dataset = json.loads((metadata_path / "dataset.json").read_text())
    DatasetMetadataSchema.validate(dataset)
    assert dataset["dataset_name"] == "eICU"
    assert dataset["dataset_version"].startswith("2.0.1:")
    assert dataset["sumeds_enrichment"]["enriched"] is True
    assert dataset["sumeds_enrichment"]["rules"] == "eICU"

    _validate_data_correctness(data_path, metadata_path)


# Codes whose events exist only to carry a measurement; an event without a value is the
# null-guard regression (wide vitals rows, or empty weight/height cells, emitting anyway).
VALUE_ONLY_CODES = (
    "ICU_ADMISSION_WEIGHT",
    "ICU_ADMISSION_HEIGHT",
    "ICU_DISCHARGE_WEIGHT",
    "SUBJECT_WEIGHT_AT_INFUSION",
)

# Charted tables keep a varchar value: numbers go to numeric_value, the rest to text_value, and a
# blank cell emits nothing, so every charted event carries exactly one of the two.
CHARTED_PREFIXES = (
    "NURSE_CHARTING//",
    "RESPIRATORY_CHARTING//",
    "NURSE_ASSESSMENT//",
    "NURSE_CARE//",
)


def _validate_data_correctness(data_path: Path, metadata_path: Path):
    """Pins the data-correctness fixes on the demo output, so a regression fails CI."""
    df = pl.scan_parquet(data_path / "**/*.parquet")

    valueless = (
        df.filter(
            pl.col("code").str.starts_with("VITALS//")
            | pl.col("code").str.starts_with("INTAKE_OUTPUT//")
            | pl.col("code").is_in(VALUE_ONLY_CODES)
        )
        .filter(pl.col("numeric_value").is_null())
        .group_by("code")
        .len()
        .collect()
    )
    assert valueless.is_empty(), f"Events emitted without a value:\n{valueless}"

    # A subject spans several unit stays, so each per-stay measurement must say which one.
    unlinked = (
        df.filter(pl.col("code").is_in(VALUE_ONLY_CODES[:3]))
        .filter(pl.col("hadm_id").is_null() | pl.col("icustay_id").is_null())
        .group_by("code")
        .len()
        .collect()
    )
    assert unlinked.is_empty(), f"Unit-stay events missing hadm_id/icustay_id:\n{unlinked}"

    n_past_history, n_textless = (
        df.filter(pl.col("code").str.starts_with("PAST_HISTORY//"))
        .select(pl.len(), pl.col("text_value").is_null().sum())
        .collect()
        .row(0)
    )
    assert n_past_history > 0, "No PAST_HISTORY events emitted; the pastHistory table is not extracted."
    assert n_textless == 0, f"{n_textless} PAST_HISTORY events carry no text_value."

    n_intake_output = (
        df.filter(pl.col("code").str.starts_with("INTAKE_OUTPUT//")).select(pl.len()).collect().item()
    )
    assert n_intake_output > 0, "No INTAKE_OUTPUT events emitted; the intakeOutput table is not extracted."

    # The exam path is the finding, so a blank text leaves an event with no value, never two.
    n_physical_exam, n_two_values = (
        df.filter(pl.col("code").str.starts_with("PHYSICAL_EXAM//"))
        .select(pl.len(), (pl.col("numeric_value").is_not_null() & pl.col("text_value").is_not_null()).sum())
        .collect()
        .row(0)
    )
    assert n_physical_exam > 0, "No PHYSICAL_EXAM events emitted; the physicalExam table is not extracted."
    assert n_two_values == 0, f"{n_two_values} PHYSICAL_EXAM events carry both a numeric and a text value."

    for prefix in CHARTED_PREFIXES:
        n_charted, n_not_one_value = (
            df.filter(pl.col("code").str.starts_with(prefix))
            .select(pl.len(), (pl.col("numeric_value").is_null() == pl.col("text_value").is_null()).sum())
            .collect()
            .row(0)
        )
        assert n_charted > 0, f"No {prefix} events emitted."
        assert n_not_one_value == 0, f"{n_not_one_value} {prefix} events carry both or neither value."

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
    undescribed = codes.filter(pl.col("description").is_null())["code"].to_list()
    assert not undescribed, f"{len(undescribed)} codes have no description, e.g. {undescribed[:5]}"
    n_with_parents = codes.filter(pl.col("parent_codes").list.len() > 0).height
    assert n_with_parents > 0, "No code in codes.parquet carries parent_codes; the dx -> ICD link is gone."

    # The suMEDS enrichment adds parents among eICU's own path codes and LOINC parents for labs.
    parents = codes.select("code", "parent_codes").explode("parent_codes").drop_nulls()
    in_dataset = parents.filter(pl.col("parent_codes").is_in(codes["code"].implode()))
    for family in ("DIAGNOSIS//", "TREATMENT//ENTERED//"):
        n = in_dataset.filter(pl.col("code").str.starts_with(family)).height
        assert n > 0, f"No {family} code got an in-dataset parent from the suMEDS hierarchy rules."
    n_loinc = parents.filter(pl.col("parent_codes").str.starts_with("LOINC")).height
    assert n_loinc > 0, "No lab code got a LOINC parent from the suMEDS crosswalk."
    # suMEDS rewrote the file in place; it must still be valid MEDS code metadata as read from disk.
    CodeMetadataSchema.validate(pq.read_table(metadata_path / "codes.parquet"))
