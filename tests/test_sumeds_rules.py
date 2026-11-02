"""The packaged suMEDS rules load in suMEDS and match the codes this ETL writes.

suMEDS validates the rules file itself (``sumeds.load_rules``) and resolves codes with the rule objects
its enrichment uses, so these tests only state what eICU's codes must resolve to. The example codes are
rendered from the ETL's own ``event_configs.yaml`` templates with synthetic values.
"""

import json
import re
from importlib.resources import files
from pathlib import Path

import polars as pl
import pytest
import yaml
from sumeds import EnrichmentConfig, OntologyRules, enrich_metadata, load_rules
from sumeds.ontology import INDEX_SCHEMA

from eICU_MEDS.code_descriptions import read_lab_descriptions

CONFIG_FP = Path(__file__).parents[1] / "src" / "eICU_MEDS" / "configs" / "event_configs.yaml"
RULES_FP = files("eICU_MEDS") / "sumeds_rules.yaml"

PATH_FAMILIES = [
    ("eicu-diagnosis-paths", "diagnosis", "diagnosis", "diagnosisstring"),
    ("eicu-admission-diagnosis-paths", "admissionDx", "admission_diagnosis", "admitdxpath"),
    ("eicu-treatment-paths", "treatment", "treatment", "treatmentstring"),
]


def _rule(rule_id: str):
    rules = load_rules(RULES_FP)
    return next(rule for rule in (*rules.vocabulary_rules, *rules.hierarchy) if rule.id == rule_id)


def _render(template: str, **values: str) -> str:
    """Render an ``f"..."`` code template of ``event_configs.yaml`` with synthetic column values."""
    body = re.fullmatch(r'\s*f"(.*)"\s*', template, flags=re.S).group(1)
    return re.sub(r"\{\s*\$(\w+)(?:\s*\?\?\s*'[^']*')?\s*\}", lambda m: values.get(m.group(1), "UNK"), body)


def _code_template(table: str, event: str) -> str:
    return yaml.safe_load(CONFIG_FP.read_text())[table][event]["code"]


@pytest.fixture
def empty_ontology_source(tmp_path):
    """An empty local pack index, so that suMEDS's enrichment fetches nothing.

    The enrichment also looks up ``VOCABULARY//CODE`` layouts in its ontology packs, which it reads from
    GitHub by default.
    """
    (tmp_path / "index.json").write_text(json.dumps({"schema": INDEX_SCHEMA, "vocabularies": {}}))
    return str(tmp_path)


def _enriched_parents(rule, codes: list[str], ontology_source: str) -> dict[str, list[str]]:
    """The parents suMEDS's own enrichment adds to ``codes`` with ``rule`` alone."""
    enriched = enrich_metadata(
        pl.LazyFrame({"code": codes}),
        EnrichmentConfig(rules=OntologyRules(hierarchy=(rule,)), ontology_source=ontology_source),
    ).collect()
    return {
        row["code"]: row["parent_codes"]
        for row in enriched.select("code", "parent_codes").to_dicts()
        if row["parent_codes"]
    }


def test_rules_load_in_sumeds():
    """The rules load in suMEDS, which rejects any malformed rule; every rule is checked below."""
    rules = load_rules(RULES_FP)
    assert [rule.id for rule in rules.vocabulary_rules] == ["eicu-lab-loinc"]
    assert {rule.id: rule.mode for rule in rules.hierarchy} == {
        rule_id: "separator" for rule_id, *_ in PATH_FAMILIES
    }


@pytest.mark.parametrize("unit", ["g/dL", "%", "K/mcL", "mm(hg)", "/hpf", "UNK"])
def test_lab_loinc_crosswalk_key_is_the_raw_lab_name(unit):
    """Every packaged lab name (the full release's vocabulary) comes back whole as the crosswalk key.

    The lazy group is exact because no lab name contains ``//`` or ends in ``/``; splitting at the
    last ``//`` instead would cut units that start with ``/`` (the full release has some).
    """
    crosswalk = _rule("eicu-lab-loinc")
    template = _code_template("lab", "lab_result")
    labnames = read_lab_descriptions()["labname"]
    assert not labnames.str.contains("//", literal=True).any()
    assert not labnames.str.ends_with("/").any()
    for labname in labnames:
        code = _render(template, labname=labname, labmeasurenamesystem=unit)
        assert code == f"LAB//RESULT//{labname}//{unit}"
        assert crosswalk.key(code) == labname, code


def test_lab_loinc_crosswalk_skips_other_lab_codes():
    drawn = _render(_code_template("lab", "lab_specimen_drawn"), labname="Hgb", labmeasurenamesystem="g/dL")
    assert drawn == "LAB//SPECIMEN_DRAWN//Hgb//g/dL"
    assert _rule("eicu-lab-loinc").key(drawn) is None


@pytest.mark.parametrize("rule_id,table,event,column", PATH_FAMILIES)
def test_path_hierarchy_rules_match_their_family_only(rule_id, table, event, column, empty_ontology_source):
    rule = _rule(rule_id)
    template = _code_template(table, event)
    leaf, middle, top = (
        _render(template, **{column: path})
        for path in ("section a|subsection b / c|leaf, d", "section a|subsection b / c", "section a")
    )
    assert rule.candidates(leaf) == [middle, top]
    assert rule.candidates(_render(template, **{column: "single section"})) == []
    others = [
        _render(_code_template(other_table, other_event), **{other_column: path})
        for other_id, other_table, other_event, other_column in PATH_FAMILIES
        if other_id != rule_id
        for path in ("a|b", "a")
    ]
    # The leaf gets the nearest path that is itself a code (the middle one is absent); the other
    # families' paths get nothing from this rule.
    assert _enriched_parents(rule, [leaf, top, *others], empty_ontology_source) == {leaf: [top]}
