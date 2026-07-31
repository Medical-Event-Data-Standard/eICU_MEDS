"""Validates the ``sources:`` block shipped in ``configs/event_configs.yaml``.

These tests are offline: constructing sources does no network I/O (PhysioNet manifests
are fetched lazily, on first ``.files`` access), so they exercise exactly what must hold
before any download starts.

The download CLI resolves interpolations per *selected* bucket, not across the whole
``sources:`` subtree — the helper here mirrors that behavior.
"""

import re

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
    from MEDS_extract.download import HTTPSource, PhysioNetSource, sources_from_spec

    monkeypatch.delenv("DATASET_DOWNLOAD_USERNAME", raising=False)
    monkeypatch.delenv("DATASET_DOWNLOAD_PASSWORD", raising=False)

    # The second entry is the casing-normalization override (see below).
    sources = sources_from_spec(_bucket_spec("demo"), key="demo")
    try:
        assert [type(s) for s in sources] == [PhysioNetSource, HTTPSource]
    finally:
        for s in sources:
            s.close()


def test_every_physionet_source_sets_a_wget_user_agent():
    """physionet.org serves credentialed ``/files/`` paths only to ``Wget/<version>``-prefixed clients.

    Without this header the credentialed download fails with a bare 403 that is
    byte-identical to the no-credentials and wrong-credentials responses — so a
    regression here looks exactly like a credential problem and costs real debugging
    time. Pin it on every entry.
    """
    raw = OmegaConf.load(EVENT_CFG)
    entries = [e for bucket in raw.sources.values() for e in bucket]
    assert entries, "no sources declared"
    for entry in entries:
        ua = entry.get("headers", {}).get("User-Agent")
        assert ua is not None, f"entry missing a User-Agent: {entry.get('type')}"
        assert re.match(r"^Wget/\d", ua), f"User-Agent must start with 'Wget/<version>', got {ua!r}"


def test_demo_infusiondrug_is_renamed_to_the_full_release_spelling():
    """The demo ships ``infusiondrug.csv.gz``; the credentialed release uses ``infusionDrug.csv.gz``.

    0.7 resolves a table by exact path, so one config prefix cannot match both spellings.
    The demo is normalized *up* to the release spelling on download. This test pins the
    three halves of that arrangement together — the exclude, the rename, and the table
    prefix that consumes it — because a silent drift in any one of them breaks only the
    other dataset, which CI never downloads.
    """
    raw = OmegaConf.load(EVENT_CFG)
    physionet, override = raw.sources["demo"]

    assert "infusiondrug.csv.gz" in physionet.exclude, "lowercase demo file must be excluded"

    (entry,) = override.urls
    assert entry.url.endswith("/infusiondrug.csv.gz"), "override must fetch the demo's lowercase name"
    assert entry.rel_path == "infusionDrug.csv.gz", "override must land under the release spelling"
    assert len(entry.sha256) == 64, "unpinned checksum would break resumed runs"

    assert "infusionDrug" in raw, "the renamed file must match a declared table prefix"
    assert "infusiondrug" not in raw, "the lowercase prefix must not linger"


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
    demo_entry = raw.sources["demo"][0]

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
