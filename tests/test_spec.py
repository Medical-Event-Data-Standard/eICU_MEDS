"""The ``eICU`` spec is registered with MEDS-Extract and declares the run ``meds-extract-run`` makes of it.

Offline: these tests load the spec and synthesize the pipeline config the way ``meds-extract-run``
does, without running it (``test_run_with_enrichment.py`` runs it on synthetic rows).
"""

from importlib.metadata import entry_points, version

import pytest
from MEDS_extract.config import MessyConfig

CANONICAL = [
    "convert_to_parquet",
    "split_and_shard_subjects",
    "convert_to_subject_sharded",
    "convert_to_MEDS_events",
    "extract_code_metadata",
    "merge_to_MEDS_cohort",
    "finalize_MEDS_metadata",
    "finalize_MEDS_data",
]


def _stage_names(pipeline: dict) -> list[str]:
    return [s if isinstance(s, str) else next(iter(s)) for s in pipeline["stages"]]


def _stage_options(pipeline: dict, name: str) -> dict:
    return next(s[name] for s in pipeline["stages"] if isinstance(s, dict) and name in s)


@pytest.fixture(scope="module")
def spec() -> MessyConfig:
    return MessyConfig.load("eICU")


def test_the_spec_is_registered_as_the_packaged_config(spec):
    (ep,) = entry_points(group="MEDS_extract.pipelines", name="eICU")
    assert ep.value == "eICU_MEDS.configs:event_configs.yaml"
    assert spec.spec_ref == "pkg://eICU_MEDS.configs.event_configs.yaml"


def test_the_run_wraps_the_canonical_stages_in_this_packages_extension_stages(spec, tmp_path):
    pipeline = spec.pipeline_config(input_dir=tmp_path / "raw", output_dir=tmp_path / "out")
    assert _stage_names(pipeline) == ["eicu_pre_meds", *CANONICAL, "eicu_sort_parent_codes", "sumeds_enrich"]
    assert _stage_options(pipeline, "sumeds_enrich")["rules"] == "eICU"
    assert _stage_options(pipeline, "split_and_shard_subjects")["n_subjects_per_shard"] == 1000
    # The two stages that read raw sources look in the pre-stage's directory first.
    for stage in ("convert_to_parquet", "extract_code_metadata"):
        assert _stage_options(pipeline, stage)["input_overlay_dirs"] == [
            str(tmp_path / "out" / "eicu_pre_meds")
        ]


def test_enrichment_can_be_left_out(spec, tmp_path):
    pipeline = spec.pipeline_config(
        input_dir=tmp_path / "raw", output_dir=tmp_path / "out", skip_stages=["sumeds_enrich"]
    )
    assert _stage_names(pipeline) == ["eicu_pre_meds", *CANONICAL, "eicu_sort_parent_codes"]


def test_the_extension_stages_are_installed_and_of_the_right_kind(spec):
    # What meds-extract-run checks before it starts: registered, main stages, known options.
    spec.etl.check_extension_stages()


@pytest.mark.parametrize(("key", "release"), [("dataset", "2.0"), ("demo", "2.0.1")])
def test_the_selected_bucket_is_stamped_with_the_package_version(spec, tmp_path, key, release):
    pipeline = spec.pipeline_config(input_dir=tmp_path / "raw", output_dir=tmp_path / "out", key=key)
    assert pipeline["etl_metadata"] == {
        "dataset_name": "eICU",
        "dataset_version": f"{release}:{version('eICU_MEDS')}",
    }
