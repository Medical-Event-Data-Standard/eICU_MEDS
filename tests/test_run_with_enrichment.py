"""``meds-extract-run spec=eICU`` runs the ETL and then suMEDS enrichment with the registered rules, offline.

The spec's ``sumeds_enrich`` post-stage enriches ``metadata/codes.parquet`` and records it in
``metadata/dataset.json``. A failed enrichment leaves the ETL output as it was and does not fail the
run, unless the stage's ``strict`` option is set; ``suMEDS-enrich OUTPUT_DIR`` enriches it later.
``skip_stages=[sumeds_enrich]`` leaves the stage out. Stage options are set at run time with
``overrides=['++stage_cfg.<option>=<value>']``, which every stage receives and only this one reads.

The raw tables are a handful of made-up rows with every column ``configs/event_configs.yaml`` reads
(nothing comes from eICU-CRD), so the run needs no download. The ontology source is a tiny pack
repository in suMEDS's ``sumeds-ontologies/0.1`` layout, built here with made-up titles for the
synthetic ICD and LOINC codes and an ``eicu/lab_loinc`` crosswalk, so suMEDS reads nothing over the
network. The e2e demo test runs the same stage against suMEDS's published ontology data.
"""

import csv
import gzip
import hashlib
import io
import json
import os
import subprocess
from pathlib import Path

import polars as pl
import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from meds import CodeMetadataSchema, DatasetMetadataSchema
from MEDS_extract.config import MessyConfig

from eICU_MEDS import EVENT_CFG
from eICU_MEDS.pre_MEDS import DX_MAP_PREFIX, LAB_DESCRIPTIONS_PREFIX

# Unit stays 1-4, one per health-system stay (the subject); MEDS-Extract needs a subject per split.
STAYS = [1, 2, 3, 4]
DX_PATH = "synthetic system|synthetic disorder"
DX_LEAF = f"{DX_PATH}|synthetic subtype"
TREATMENT_PATH = "synthetic therapy|synthetic class"
TREATMENT_LEAF = f"{TREATMENT_PATH}|synthetic drug"

# The rows that matter for enrichment; every other table gets one generic row for stay 1.
TABLES = {
    "patient": [
        {
            "patientunitstayid": stay,
            "patienthealthsystemstayid": 100 + stay,
            "gender": "Female" if stay % 2 else "Male",
            "age": "> 89" if stay == 4 else str(50 + stay),
            "ethnicity": "Synthetic ethnicity",
            "hospitalid": 1,
            "wardid": 10,
            "hospitaladmitoffset": -120,
            "hospitaladmitsource": "Synthetic source",
            "hospitaldischargeyear": 2015,
            "hospitaldischargetime24": "12:00:00",
            "hospitaldischargeoffset": 3000,
            "hospitaldischargelocation": "Synthetic location",
            "hospitaldischargestatus": "Expired" if stay == 3 else "Alive",
            "unitadmitsource": "Synthetic source",
            "unitstaytype": "admit",
            "admissionheight": 170.5,
            "admissionweight": 70.5,
            "dischargeweight": 69.5,
            "unitdischargeoffset": 2000,
            "unitdischargelocation": "Synthetic location",
            "unitdischargestatus": "Alive",
        }
        for stay in STAYS
    ],
    "hospital": [
        {
            "hospitalid": 1,
            "numbedscategory": "Synthetic",
            "region": "Synthetic",
            "teachingstatus": "Synthetic",
        }
    ],
    # `icd9code` is read by pre-MEDS for the ICD parents of diagnosis codes.
    "diagnosis": [
        {
            "patientunitstayid": 1,
            "diagnosisoffset": 10,
            "diagnosispriority": "Primary",
            "diagnosisstring": DX_PATH,
            "icd9code": "486, J18.9",
        },
        {
            "patientunitstayid": 2,
            "diagnosisoffset": 20,
            "diagnosispriority": "Major",
            "diagnosisstring": DX_LEAF,
            "icd9code": "401.9, I10",
        },
        {
            "patientunitstayid": 3,
            "diagnosisoffset": 30,
            "diagnosispriority": "Other",
            "diagnosisstring": "synthetic system|uncoded",
            "icd9code": "",
        },
    ],
    "lab": [
        {
            "patientunitstayid": stay,
            "labresultoffset": 40,
            "labresultrevisedoffset": 45,
            "labtypeid": 1,
            "labname": labname,
            "labmeasurenamesystem": "g/dL",
            "labresult": 12.5,
            "labresulttext": "12.5",
        }
        for stay in STAYS
        for labname in ("Hgb", "synthetic lab")
    ],
    "treatment": [
        {"patientunitstayid": 1, "treatmentoffset": 50, "treatmentstring": TREATMENT_PATH},
        {"patientunitstayid": 2, "treatmentoffset": 60, "treatmentstring": TREATMENT_LEAF},
    ],
}

# vocabulary -> release -> code -> parent (None for a top-level code); made-up titles.
PACKS = {
    "ICD9CM": ("v32", {"401": None, "401.9": "401", "486": None}),
    "ICD10CM": ("fy2016-fy2027", {"I10": None, "J18": None, "J18.9": "J18"}),
    "LOINC": ("2.83", {"718-7": None}),
}
LAB_LOINC = [{"labname": "Hgb", "loinc_code": "718-7"}]


def title(vocabulary: str, code: str) -> str:
    return f"Synthetic {vocabulary} title {code}"


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _csv(rows: list[dict], columns: list[str]) -> bytes:
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=columns, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return buffer.getvalue().encode()


def _generic_value(table: str, column: str) -> int | str:
    """A value of the type the column has in eICU-CRD: ids, offsets and measurements are numbers."""
    numeric = column.endswith(("id", "offset", "numeric")) or table.startswith("vital")
    return 1 if numeric else f"Synthetic {column}"


@pytest.fixture(scope="session")
def raw_dir(tmp_path_factory) -> Path:
    """Every raw table the config reads, with every column it reads (the pre-stage writes the rest)."""
    raw = tmp_path_factory.mktemp("eicu_raw")
    for table, columns in MessyConfig.load(EVENT_CFG).needed_source_columns().items():
        if table in (DX_MAP_PREFIX, LAB_DESCRIPTIONS_PREFIX):
            continue
        rows = TABLES.get(table) or [{c: _generic_value(table, c) for c in columns}]
        header = sorted(set(columns) | set(rows[0]))
        (raw / f"{table}.csv.gz").write_bytes(gzip.compress(_csv(rows, header)))
    return raw


@pytest.fixture(scope="session")
def ontology_source(tmp_path_factory) -> Path:
    """A local ``sumeds-ontologies/0.1`` repository holding the synthetic packs and crosswalk."""
    root = tmp_path_factory.mktemp("ontologies")
    columns = ["code", "description", "parent", "first_year", "last_year"]
    vocabularies = {}
    for vocabulary, (release, rows) in PACKS.items():
        directory = root / vocabulary / release
        directory.mkdir(parents=True)
        by_shard: dict[str, list[dict]] = {}
        for code, parent in rows.items():
            row = {"code": code, "description": title(vocabulary, code), "parent": parent or ""}
            by_shard.setdefault(code[:1], []).append(row)
        shards = {}
        for key, shard_rows in by_shard.items():
            data = _csv(shard_rows, columns)
            (directory / f"{key}.csv").write_bytes(data)
            shards[key] = {
                "file": f"{key}.csv",
                "rows": len(shard_rows),
                "bytes": len(data),
                "sha256": _sha256(data),
            }
        manifest = {
            "schema": "sumeds-ontology-pack/0.1",
            "vocabulary": vocabulary,
            "release": release,
            "columns": columns,
            "fields": {"code": "code", "description": "description", "parent": "parent"},
            "shard_key_length": 1,
            "rows": len(rows),
            "bytes": sum(s["bytes"] for s in shards.values()),
            "shards": shards,
        }
        manifest_bytes = json.dumps(manifest).encode()
        (directory / "manifest.json").write_bytes(manifest_bytes)
        vocabularies[vocabulary] = {
            "manifest": f"{vocabulary}/{release}/manifest.json",
            "sha256": _sha256(manifest_bytes),
            "release": release,
            "rows": manifest["rows"],
            "bytes": manifest["bytes"],
        }

    crosswalk_dir = root / "crosswalks" / "eicu"
    crosswalk_dir.mkdir(parents=True)
    data = _csv(LAB_LOINC, ["labname", "loinc_code"])
    (crosswalk_dir / "lab_loinc.csv").write_bytes(data)
    tables = {"lab_loinc": {"file": "lab_loinc.csv", "rows": len(LAB_LOINC), "sha256": _sha256(data)}}
    manifest_bytes = json.dumps(
        {"schema": "sumeds-crosswalk/0.1", "dataset": "eicu", "tables": tables}
    ).encode()
    (crosswalk_dir / "manifest.json").write_bytes(manifest_bytes)
    crosswalks = {"eicu": {"manifest": "crosswalks/eicu/manifest.json", "sha256": _sha256(manifest_bytes)}}

    index = {"schema": "sumeds-ontologies/0.1", "vocabularies": vocabularies, "crosswalks": crosswalks}
    (root / "index.json").write_text(json.dumps(index))
    return root


def _etl(output: Path, raw_dir: Path, *args: str, **env: str) -> subprocess.CompletedProcess:
    command = [
        "meds-extract-run",
        "spec=eICU",
        f"output_dir={output}",
        "do_download=false",
        f"input_dir={raw_dir}",
        *args,
    ]
    return subprocess.run(command, capture_output=True, text=True, env={**os.environ, **env})


def _run(output: Path, raw_dir: Path, *args: str, **env: str) -> Path:
    out = _etl(output, raw_dir, *args, **env)
    assert out.returncode == 0, f"{out.args} failed:\n{out.stdout}\n{out.stderr}"
    return output


def _pipeline_log(output: Path) -> str:
    """Post-stages write their output only to the pipeline log, not to the console."""
    return (output / ".logs" / "pipeline.log").read_text()


@pytest.fixture(scope="session")
def plain_output(tmp_path_factory, raw_dir: Path) -> Path:
    return _run(tmp_path_factory.mktemp("eicu_plain"), raw_dir, "skip_stages=[sumeds_enrich]")


@pytest.fixture(scope="session")
def enriched_output(tmp_path_factory, raw_dir: Path, ontology_source: Path) -> Path:
    return _run(
        tmp_path_factory.mktemp("eicu_enriched"),
        raw_dir,
        f"overrides=['++stage_cfg.ontology_source={ontology_source}']",
    )


def _codes_bytes(output: Path) -> bytes:
    return (output / "metadata" / "codes.parquet").read_bytes()


def _dataset(output: Path) -> dict:
    """``metadata/dataset.json``, which must stay valid MEDS dataset metadata."""
    dataset = json.loads((output / "metadata" / "dataset.json").read_text())
    DatasetMetadataSchema.validate(dataset)
    return dataset


def _codes(output: Path) -> dict[str, dict]:
    return {row["code"]: row for row in pl.read_parquet(output / "metadata" / "codes.parquet").to_dicts()}


def _names(row: dict) -> dict[str, str]:
    """``parent_names`` as a dict: Polars 2 reads the map as one, Polars 1 as key/value structs."""
    names = row.get("parent_names") or {}
    return names if isinstance(names, dict) else {entry["key"]: entry["value"] for entry in names}


# What the `MEDS_extract-eICU` runner of 0.2.0 (at 0c0b55c, `do_enrich=False`) wrote for the
# synthetic rows above. Regenerate it only for a deliberate change to the output, with
# `EICU_MEDS_UPDATE_SNAPSHOT=1 pytest tests/test_run_with_enrichment.py`, and say why in the PR.
SNAPSHOT_FP = Path(__file__).parent / "fixtures" / "synthetic_etl_snapshot.json"


def _snapshot(output: Path) -> dict:
    """Every event (non-null fields), the splits and each code's description and parent codes.

    ``code_template`` is left out: the runner left it null on the literal codes and on lab names
    outside the lab table, which now carry their code expression, as every other code does.
    """
    data = pl.concat([pl.read_parquet(fp) for fp in sorted((output / "data").rglob("*.parquet"))])
    data = data.sort(data.columns, nulls_last=True)
    events = [
        {k: v.isoformat() if k == "time" and v is not None else v for k, v in row.items() if v is not None}
        for row in data.iter_rows(named=True)
    ]
    splits = pl.read_parquet(output / "metadata" / "subject_splits.parquet").sort("subject_id")
    codes = pl.read_parquet(output / "metadata" / "codes.parquet").sort("code")
    return {
        "events": events,
        "subject_splits": splits.rows(),
        "codes": codes.select("code", "description", "parent_codes").rows(),
    }


def test_output_matches_the_runner_it_replaces(plain_output: Path):
    """``meds-extract-run spec=eICU`` writes what ``MEDS_extract-eICU`` wrote: events and code metadata."""
    snapshot = json.loads(json.dumps(_snapshot(plain_output)))  # tuples to lists, as read back
    if os.environ.get("EICU_MEDS_UPDATE_SNAPSHOT"):
        SNAPSHOT_FP.parent.mkdir(exist_ok=True)
        SNAPSHOT_FP.write_text(json.dumps(snapshot, indent=1) + "\n")
    expected = json.loads(SNAPSHOT_FP.read_text())
    assert snapshot["codes"] == expected["codes"]
    assert snapshot["subject_splits"] == expected["subject_splits"]
    assert snapshot["events"] == expected["events"]


def test_codes_parquet_is_replaced_by_valid_meds_metadata(enriched_output: Path):
    metadata = enriched_output / "metadata"
    table = pq.read_table(metadata / "codes.parquet")
    CodeMetadataSchema.validate(table)
    assert table.schema.field("parent_names").type == pa.map_(pa.string(), pa.string())
    assert not list(metadata.glob(".*enriched*")), "the temporary enrichment output was left behind"


def test_data_matches_the_plain_etl(enriched_output: Path, plain_output: Path):
    def events(output: Path) -> pl.DataFrame:
        key = ["subject_id", "time", "code"]
        return (
            pl.concat([pl.read_parquet(fp) for fp in (output / "data").rglob("*.parquet")])
            .select(key)
            .sort(key)
        )

    assert events(enriched_output).equals(events(plain_output))


def test_the_etl_describes_every_code(plain_output: Path):
    """Literal codes from their constant ``_self`` blocks; a lab name outside the lab table generically."""
    codes = _codes(plain_output)
    assert not [code for code, row in codes.items() if not row["description"]]
    assert codes["MEDS_BIRTH"]["description"].startswith("Birth (MEDS standard code)")
    assert codes["VITALS//PERIODIC//HEARTRATE"]["description"] == (
        "Heart rate, beats per minute (vitalPeriodic.heartRate)"
    )
    assert codes["LAB//RESULT//Hgb//g/dL"]["description"] == "Hemoglobin (eICU lab.labName 'Hgb')"
    assert codes["LAB//RESULT//synthetic lab//g/dL"]["description"] == (
        "Lab result (eICU lab.labName 'synthetic lab')"
    )
    assert codes["LAB//SPECIMEN_DRAWN//synthetic lab//g/dL"]["description"] == (
        "Lab specimen drawn (eICU lab.labName 'synthetic lab')"
    )


def test_enrichment_keeps_what_the_etl_wrote(enriched_output: Path, plain_output: Path):
    """Descriptions are never overwritten, and the ETL's parents stay first, in their order."""
    plain, enriched = _codes(plain_output), _codes(enriched_output)
    assert plain.keys() == enriched.keys()
    for code, row in plain.items():
        assert enriched[code]["description"] == row["description"], code
        parents = row["parent_codes"] or []
        assert (enriched[code]["parent_codes"] or [])[: len(parents)] == parents, code


def test_enrichment_adds_vocabulary_and_same_table_parents(enriched_output: Path, plain_output: Path):
    plain, enriched = _codes(plain_output), _codes(enriched_output)

    # The ETL's ICD references get their names and ancestors from the packs, and the diagnosis
    # path gets the nearest shorter path that is itself a code.
    leaf = enriched[f"DIAGNOSIS//{DX_LEAF}"]
    assert plain[f"DIAGNOSIS//{DX_LEAF}"]["parent_codes"] == ["ICD10CM/I10", "ICD9CM/401.9"]
    assert set(leaf["parent_codes"]) == {
        "ICD10CM/I10",
        "ICD9CM/401.9",
        "ICD9CM//401",
        f"DIAGNOSIS//{DX_PATH}",
    }
    assert _names(leaf) == {
        "ICD10CM/I10": title("ICD10CM", "I10"),
        "ICD9CM/401.9": title("ICD9CM", "401.9"),
        "ICD9CM//401": title("ICD9CM", "401"),
    }
    treatment = enriched[f"TREATMENT//ENTERED//{TREATMENT_LEAF}"]
    assert treatment["parent_codes"] == [f"TREATMENT//ENTERED//{TREATMENT_PATH}"]

    # The lab crosswalk links Hgb to LOINC; a lab name outside it is left alone.
    hgb = enriched["LAB//RESULT//Hgb//g/dL"]
    assert hgb["parent_codes"] == ["LOINC//718-7"]
    assert _names(hgb) == {"LOINC//718-7": title("LOINC", "718-7")}
    assert (hgb["vocabulary_id"], hgb["concept_code"]) == ("LOINC", "718-7")
    assert not enriched["LAB//RESULT//synthetic lab//g/dL"]["parent_codes"]


def test_dataset_json_records_the_enrichment(enriched_output: Path, plain_output: Path):
    assert "sumeds_enrichment" not in _dataset(plain_output)
    record = _dataset(enriched_output)["sumeds_enrichment"]
    assert record["enriched"] is True
    assert record["rules"] == "eICU"
    assert record["ontology_source"] == "local"
    coverage = record["coverage"]
    assert coverage["codes"] == len(_codes(enriched_output))
    assert coverage["described"][0] <= coverage["described"][1]
    assert coverage["with_parent_codes"][0] < coverage["with_parent_codes"][1]


def test_failed_enrichment_keeps_the_etl_output_and_is_enriched_later(
    tmp_path: Path, raw_dir: Path, ontology_source: Path, plain_output: Path, enriched_output: Path
):
    """No ontology data (as without internet): the run succeeds, unenriched, and says so.

    The pack source comes from ``SUMEDS_ONTOLOGY_SOURCE`` here, the environment variable suMEDS reads
    when the stage sets no ``ontology_source``.
    """
    missing = tmp_path / "no_ontologies"
    output = tmp_path / "eicu"
    out = _etl(output, raw_dir, SUMEDS_ONTOLOGY_SOURCE=str(missing))
    assert out.returncode == 0, f"{out.args} failed:\n{out.stdout}\n{out.stderr}"
    assert "suMEDS-enrich: warning: enrichment failed" in _pipeline_log(output)
    assert _codes_bytes(output) == _codes_bytes(plain_output)
    record = _dataset(output)["sumeds_enrichment"]
    assert record["enriched"] is False
    assert record["rules"] == "eICU"
    assert record["retry"] == "suMEDS-enrich <MEDS_ROOT>"
    assert str(missing) not in json.dumps(record)

    # The stage counts as done, so running the same command again, now with the data, does not retry.
    out = _etl(output, raw_dir, SUMEDS_ONTOLOGY_SOURCE=str(ontology_source))
    assert out.returncode == 0, f"{out.args} failed:\n{out.stdout}\n{out.stderr}"
    assert _dataset(output)["sumeds_enrichment"]["enriched"] is False

    # suMEDS-enrich does, with the recorded rules and options; a local pack source is given again.
    retry = ["suMEDS-enrich", str(output), "--ontology-source", str(ontology_source)]
    out = subprocess.run(retry, capture_output=True, text=True)
    assert out.returncode == 0, f"{retry} failed:\n{out.stdout}\n{out.stderr}"
    assert pl.read_parquet(output / "metadata" / "codes.parquet").equals(
        pl.read_parquet(enriched_output / "metadata" / "codes.parquet")
    )
    record = _dataset(output)["sumeds_enrichment"]
    assert record["enriched"] is True
    assert record["rules"] == "eICU"


def test_strict_enrichment_fails_the_run(tmp_path: Path, raw_dir: Path, plain_output: Path):
    missing = tmp_path / "no_ontologies"
    output = tmp_path / "eicu"
    out = _etl(
        output,
        raw_dir,
        f"overrides=['++stage_cfg.strict=true','++stage_cfg.ontology_source={missing}']",
    )
    assert out.returncode != 0
    assert "suMEDS-enrich: error:" in out.stdout + out.stderr
    assert _codes_bytes(output) == _codes_bytes(plain_output)
    assert "sumeds_enrichment" not in _dataset(output)
    assert not (output / ".logs" / "sumeds_enrich.done").exists()


def test_a_skipped_enrichment_runs_when_the_skip_is_dropped(
    tmp_path: Path, raw_dir: Path, ontology_source: Path
):
    """The same command without ``skip_stages`` runs only the missing stage on a finished build."""
    output = _run(tmp_path / "eicu", raw_dir, "skip_stages=[sumeds_enrich]")
    assert "sumeds_enrichment" not in _dataset(output)
    events = {fp: fp.read_bytes() for fp in (output / "data").rglob("*.parquet")}
    _run(output, raw_dir, f"overrides=['++stage_cfg.ontology_source={ontology_source}']")
    assert _dataset(output)["sumeds_enrichment"]["enriched"] is True
    assert {fp: fp.read_bytes() for fp in (output / "data").rglob("*.parquet")} == events
