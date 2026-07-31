"""Validates the ``sources:`` block shipped in ``configs/event_configs.yaml``.

These tests are offline: constructing sources does no network I/O (PhysioNet manifests
are fetched lazily, on first ``.files`` access), so they exercise exactly what must hold
before any download starts.

The download CLI resolves interpolations per *selected* bucket, not across the whole
``sources:`` subtree — the helper here mirrors that behavior.
"""

import pytest
from omegaconf import OmegaConf
from omegaconf.errors import InterpolationResolutionError

from eICU_MEDS import EVENT_CFG


def _bucket_spec(*keys: str) -> dict:
    """Resolve interpolations for only the selected buckets, mirroring the CLI."""
    raw = OmegaConf.load(EVENT_CFG)
    return {"sources": {k: OmegaConf.to_container(raw.sources[k], resolve=True) for k in keys}}


def test_demo_constructs_without_credentials(monkeypatch):
    """The demo bucket must construct with no credential env vars set — demo and CI runs never need PhysioNet
    credentials."""
    from MEDS_extract.download import PhysioNetSource, sources_from_spec

    monkeypatch.delenv("DATASET_DOWNLOAD_USERNAME", raising=False)
    monkeypatch.delenv("DATASET_DOWNLOAD_PASSWORD", raising=False)

    sources = sources_from_spec(_bucket_spec("demo"), key="demo")
    try:
        assert [type(s) for s in sources] == [PhysioNetSource]
    finally:
        for s in sources:
            s.close()


def test_dataset_requires_credentials(monkeypatch):
    """The credentialed dataset bucket fails fast (clear missing-env-var error) without credentials, and
    constructs once they are set — the intended UX for both cases."""
    from MEDS_extract.download import PhysioNetSource, sources_from_spec

    monkeypatch.delenv("DATASET_DOWNLOAD_USERNAME", raising=False)
    monkeypatch.delenv("DATASET_DOWNLOAD_PASSWORD", raising=False)

    with pytest.raises(InterpolationResolutionError, match="DATASET_DOWNLOAD_USERNAME"):
        _bucket_spec("dataset")

    monkeypatch.setenv("DATASET_DOWNLOAD_USERNAME", "someone")
    monkeypatch.setenv("DATASET_DOWNLOAD_PASSWORD", "hunter2")
    sources = sources_from_spec(_bucket_spec("dataset"), key="dataset")
    try:
        assert [type(s) for s in sources] == [PhysioNetSource]
    finally:
        for s in sources:
            s.close()


def test_buckets_point_at_the_declared_dataset_versions():
    """The bucket URLs must track the versions recorded in ``dataset.yaml``.

    These are the same two numbers that go into ``DATASET_VERSION`` on the extracted
    cohort, so a bumped URL that forgets ``dataset.yaml`` (or vice versa) would silently
    mislabel the output.
    """
    from eICU_MEDS import dataset_info

    raw = OmegaConf.load(EVENT_CFG)
    (dataset_entry,) = raw.sources["dataset"]
    (demo_entry,) = raw.sources["demo"]

    assert dataset_entry.type == "physionet"
    assert demo_entry.type == "physionet"
    assert dataset_entry.base_url.endswith(f"/eicu-crd/{dataset_info.raw_dataset_version}")
    assert demo_entry.base_url.endswith(f"/eicu-crd-demo/{dataset_info.demo_dataset_version}")


def test_no_common_bucket():
    """eICU pulls no external concept-map metadata, so there is no ``common`` bucket.

    ``sources_from_spec`` always appends ``common`` when present; asserting its absence
    keeps a stray entry from silently joining every download.
    """
    raw = OmegaConf.load(EVENT_CFG)
    assert set(raw.sources.keys()) == {"dataset", "demo"}
