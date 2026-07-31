import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory


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
