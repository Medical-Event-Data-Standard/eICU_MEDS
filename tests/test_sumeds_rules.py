"""The packaged suMEDS rules file parses and its rules match the codes this ETL writes.

suMEDS is not a dependency of the ETL, so the structural checks mirror its loader
(``sumeds.ontology.load_rules``, format ``sumeds-rules/0.1``) with ``yaml`` and ``re`` only, and
the example codes are rendered from the ETL's own ``event_configs.yaml`` templates with synthetic
values.
"""

import re
from importlib.resources import files
from pathlib import Path

import pytest
import yaml

from eICU_MEDS.code_descriptions import read_lab_descriptions

CONFIG_FP = Path(__file__).parents[1] / "src" / "eICU_MEDS" / "configs" / "event_configs.yaml"

SCHEMA = "sumeds-rules/0.1"
TOP_LEVEL_KEYS = {"schema", "ontology", "crosswalk", "hierarchy"}
ONTOLOGY_KEYS = {"id", "pattern", "vocabulary", "normalize", "validate"}
CROSSWALK_KEYS = {"id", "pattern", "table", "key_column", "code_column", "vocabulary", "where"} | {
    "normalize",
    "validate",
}
HIERARCHY_KEYS = {"id", "pattern", "group", "mode", "separator", "ignore", "nearest_only"} | {
    "from_ontology",
    "rules",
}
VALIDATE_MODES = {"pack", "athena", "none"}
TEMPLATE_REFERENCE = re.compile(r"\\(?:g<([^>]*)>|(\d{1,2}))")


def _rules() -> dict:
    return yaml.safe_load((files("eICU_MEDS") / "sumeds_rules.yaml").read_text(encoding="utf-8"))


def _rule(rule_id: str) -> dict:
    rules = _rules()
    for kind in ("ontology", "crosswalk", "hierarchy"):
        for rule in rules.get(kind) or []:
            if rule["id"] == rule_id:
                return rule
    raise KeyError(rule_id)


def _render(template: str, **values: str) -> str:
    """Render an ``f"..."`` code template of ``event_configs.yaml`` with synthetic column values."""
    body = re.fullmatch(r'\s*f"(.*)"\s*', template, flags=re.S).group(1)
    return re.sub(r"\{\s*\$(\w+)(?:\s*\?\?\s*'[^']*')?\s*\}", lambda m: values.get(m.group(1), "UNK"), body)


def _code_template(table: str, event: str) -> str:
    return yaml.safe_load(CONFIG_FP.read_text())[table][event]["code"]


def _separator_parents(rule: dict, code: str) -> list[str]:
    """The candidate parents suMEDS tries for a ``separator`` rule, nearest first."""
    match = re.search(rule["pattern"], code)
    if match is None:
        return []
    start, end = match.span(rule["group"])
    parts = match.group(rule["group"]).split(rule["separator"])
    stems = [rule["separator"].join(parts[:n]) for n in range(len(parts) - 1, 0, -1)]
    return [code[:start] + stem + code[end:] for stem in stems]


def test_rules_file_is_valid_sumeds_rules():
    rules = _rules()
    assert rules["schema"] == SCHEMA
    assert set(rules) <= TOP_LEVEL_KEYS
    seen = set()
    vocabulary_ids = set()
    for kind, allowed, group in (("ontology", ONTOLOGY_KEYS, "code"), ("crosswalk", CROSSWALK_KEYS, "key")):
        for rule in rules.get(kind) or []:
            assert set(rule) <= allowed, (rule["id"], set(rule) - allowed)
            assert rule["id"] and rule["id"] not in seen
            seen.add(rule["id"])
            vocabulary_ids.add(rule["id"])
            assert group in re.compile(rule["pattern"]).groupindex, rule["id"]
            assert rule["vocabulary"] and "/" not in rule["vocabulary"]
            assert rule.get("validate", "pack") in VALIDATE_MODES
            for step in rule.get("normalize") or []:
                assert set(step) == {"pattern", "replace"}
                compiled = re.compile(step["pattern"])
                for name, number in TEMPLATE_REFERENCE.findall(step["replace"]):
                    assert (int(number) <= compiled.groups) if number else (name in compiled.groupindex)
            if kind == "crosswalk":
                assert re.fullmatch(r"[A-Za-z0-9][\w.-]*/[A-Za-z0-9][\w.-]*", rule["table"])
                assert rule["key_column"] and rule["code_column"]
    for rule in rules.get("hierarchy") or []:
        assert set(rule) <= HIERARCHY_KEYS, (rule["id"], set(rule) - HIERARCHY_KEYS)
        assert rule["id"] and rule["id"] not in seen
        seen.add(rule["id"])
        assert isinstance(rule.get("nearest_only", True), bool)
        if rule.get("from_ontology"):
            assert set(rule) <= {"id", "from_ontology", "nearest_only", "rules"}
            assert set(rule.get("rules") or []) <= vocabulary_ids
            continue
        assert rule["group"] in re.compile(rule["pattern"]).groupindex, rule["id"]
        if rule.get("mode", "separator" if "separator" in rule else None) == "separator":
            assert rule["separator"] and "ignore" not in rule
        else:
            assert rule["mode"] == "prefix" and "separator" not in rule


@pytest.mark.parametrize("unit", ["g/dL", "%", "K/mcL", "mm(hg)", "/hpf", "UNK"])
def test_lab_loinc_crosswalk_key_is_the_raw_lab_name(unit):
    """Every packaged lab name (the full release's vocabulary) comes back whole as the crosswalk key.

    The lazy group is exact because no lab name contains ``//`` or ends in ``/``; splitting at the
    last ``//`` instead would cut units that start with ``/`` (the full release has some).
    """
    pattern = re.compile(_rule("eicu-lab-loinc")["pattern"])
    template = _code_template("lab", "lab_result")
    labnames = read_lab_descriptions()["labname"]
    assert not labnames.str.contains("//", literal=True).any()
    assert not labnames.str.ends_with("/").any()
    for labname in labnames:
        code = _render(template, labname=labname, labmeasurenamesystem=unit)
        assert code == f"LAB//RESULT//{labname}//{unit}"
        assert pattern.search(code).group("key") == labname, code


def test_lab_loinc_crosswalk_skips_other_lab_codes():
    pattern = re.compile(_rule("eicu-lab-loinc")["pattern"])
    drawn = _render(_code_template("lab", "lab_specimen_drawn"), labname="Hgb", labmeasurenamesystem="g/dL")
    assert drawn == "LAB//SPECIMEN_DRAWN//Hgb//g/dL"
    assert pattern.search(drawn) is None


@pytest.mark.parametrize(
    "rule_id,table,event,column",
    [
        ("eicu-diagnosis-paths", "diagnosis", "diagnosis", "diagnosisstring"),
        ("eicu-admission-diagnosis-paths", "admissionDx", "admission_diagnosis", "admitdxpath"),
        ("eicu-treatment-paths", "treatment", "treatment", "treatmentstring"),
    ],
)
def test_path_hierarchy_rules_match_their_family_only(rule_id, table, event, column):
    rule = _rule(rule_id)
    template = _code_template(table, event)
    code = _render(template, **{column: "section a|subsection b / c|leaf, d"})
    assert _separator_parents(rule, code) == [
        _render(template, **{column: "section a|subsection b / c"}),
        _render(template, **{column: "section a"}),
    ]
    assert _separator_parents(rule, _render(template, **{column: "single section"})) == []
    others = {
        ("diagnosis", "diagnosis", "diagnosisstring"),
        ("admissionDx", "admission_diagnosis", "admitdxpath"),
        ("treatment", "treatment", "treatmentstring"),
    } - {(table, event, column)}
    for other_table, other_event, other_column in others:
        other = _render(_code_template(other_table, other_event), **{other_column: "a|b"})
        assert re.search(rule["pattern"], other) is None, (rule_id, other)
