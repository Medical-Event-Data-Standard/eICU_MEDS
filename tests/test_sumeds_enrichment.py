"""The eICU side of the opt-in suMEDS step (``do_enrich=True``): the packaged rules and the install hint.

suMEDS enriches ``codes.parquet`` in place itself (tested in suMEDS); the e2e demo test runs the step and
checks its result. The packaged rules are checked rule by rule in ``test_sumeds_rules.py``.
"""

import sys

import pytest

from eICU_MEDS.sumeds_enrichment import sumeds_config


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
