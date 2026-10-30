# eICU MEDS Extraction ETL

[![PyPI - Version](https://img.shields.io/pypi/v/eICU-MEDS)](https://pypi.org/project/eICU-MEDS/)
[![Documentation Status](https://readthedocs.org/projects/etl-meds/badge/?version=latest)](https://etl-meds.readthedocs.io/en/stable/?badge=stable)
![Static Badge](https://img.shields.io/badge/MEDS-0.3.3-blue)
[![codecov](https://codecov.io/gh/Medical-Event-Data-Standard/eICU_MEDS/graph/badge.svg?token=RW6JXHNT0W)](https://codecov.io/gh/Medical-Event-Data-Standard/eICU_MEDS)
[![tests](https://github.com/Medical-Event-Data-Standard/eICU_MEDS/actions/workflows/tests.yaml/badge.svg)](https://github.com/Medical-Event-Data-Standard/eICU_MEDS/actions/workflows/tests.yml)
[![code-quality](https://github.com/Medical-Event-Data-Standard/eICU_MEDS/actions/workflows/code-quality-main.yaml/badge.svg)](https://github.com/Medical-Event-Data-Standard/eICU_MEDS/actions/workflows/code-quality-main.yaml)
![python](https://img.shields.io/badge/-Python_3.11-blue?logo=python&logoColor=white)
[![license](https://img.shields.io/badge/License-MIT-green.svg?labelColor=gray)](https://github.com/Medical-Event-Data-Standard/eICU_MEDS#license)
[![PRs](https://img.shields.io/badge/PRs-welcome-brightgreen.svg)](https://github.com/Medical-Event-Data-Standard/eICU_MEDS/pulls)
[![contributors](https://img.shields.io/github/contributors/Medical-Event-Data-Standard/eICU_MEDS.svg)](https://github.com/Medical-Event-Data-Standard/eICU_MEDS/graphs/contributors)
[![DOI](https://zenodo.org/badge/904327794.svg)](https://doi.org/10.5281/zenodo.17535692)

This repository contains the code for downloading the
[eICU dataset](https://physionet.org/content/eicu-crd/2.0/) from PhysioNet and transforming it into the
[Medical Event Data Standard (MEDS)](https://medical-event-data-standard.org/) format.

```bash
pip install eICU-MEDS
export DATASET_DOWNLOAD_USERNAME=$PHYSIONET_USERNAME
export DATASET_DOWNLOAD_PASSWORD=$PHYSIONET_PASSWORD
MEDS_extract-eICU root_output_dir=data/eicu_meds
```

When you run this, the program will:

1. Download the raw eICU-CRD tables this ETL reads (see
    [Which eICU tables are extracted](#which-eicu-tables-are-extracted)) for the currently supported version into
    `$ROOT_OUTPUT_DIR/raw_input` (via MEDS-Extract's download layer; files that already
    exist and verify against PhysioNet's `SHA256SUMS.txt` are skipped, so an interrupted
    run resumes rather than restarting).
2. Construct the MEDS cohort directly from those raw files — every transformation, join,
    and pseudotime derivation is declared in `src/eICU_MEDS/configs/event_configs.yaml` —
    and write it to `$ROOT_OUTPUT_DIR/MEDS_output`.

The public [demo](https://physionet.org/content/eicu-crd-demo/2.0.1/) needs no
credentials:

```bash
MEDS_extract-eICU root_output_dir=data/eicu_demo do_demo=True
```

You can also point the raw and output directories somewhere else directly.
`root_output_dir` is still required, since the intermediate `pre_MEDS` tables and the
logs are written under it:

```bash
MEDS_extract-eICU root_output_dir=$ROOT_OUTPUT_DIR \
    raw_input_dir=$RAW_INPUT_DIR MEDS_output_dir=$MEDS_OUTPUT_DIR
```

## A note on time in eICU

eICU contains **no absolute timestamps**. Every table records an integer offset in
minutes relative to unit admission, and health-system stays are ordered only at
*year* granularity. This pipeline therefore:

- uses the **health system stay** (`patienthealthsystemstayid`) as the MEDS `subject_id`,
    since events are well ordered only within a stay; and
- anchors each stay at a constant, arbitrary pseudo-date (December 31 of the recorded
    discharge year, at the recorded discharge clock time) and derives every event time by
    offsetting from it.

**Only relative time differences within a subject are meaningful.** Absolute dates in the
output are not real and must not be interpreted as such.

## Death

`MEDS_DEATH` is emitted at hospital discharge when `hospitalDischargeStatus == "Expired"`, and from
no other field. The
[documentation](https://github.com/MIT-LCP/eicu-code/blob/main/website/content/eicutables/patient.md)
describes `Expired` as the patient's condition on leaving the hospital, and on the full release it
agrees with the independently populated `hospitalDischargeLocation == "Death"` on every one of the
200,859 rows (18,004 deaths).

Hospital rather than unit status is the key because it is the more complete of the two: 7,315 rows
record a unit discharge `Alive` followed by a hospital death, which unit status would miss.

The converse is deliberately **not** used. 222 rows (0.1%) have `unitDischargeStatus == "Expired"`
while hospital status is `Alive` or blank, and these emit no `MEDS_DEATH`. They contradict
themselves: in 142 of them the hospital stay continues past the unit discharge at which the
patient supposedly died, some to a discharge home. Rows with a blank hospital status (1,751) are
likewise not treated as deaths. See
[#11](https://github.com/Medical-Event-Data-Standard/eICU_MEDS/issues/11).

## Vital signs: only measurements that happened

`vitalPeriodic` and `vitalAperiodic` are wide tables — one row per timestamp, one column per
vital sign — and most cells are empty. A row that recorded a heart rate still carries columns
for intracranial pressure, end-tidal CO2 and the pulmonary-artery pressures, which need
invasive lines that most patients never have.

Each vital's code is therefore guarded so that a missing cell emits **no event**, rather than an
event asserting the measurement was taken with no value attached. Without the guard the full
cohort carries 2.60 billion vitals rows of which **69.8% are empty**; with it, 785 million, all
of them real. Per-vital emptiness in the raw data ranges from 0.5% (heart rate) to 98.2%
(intracranial pressure).

The guard is written `if $x == $x`, which is true for any present value — including `0`, `inf`
and `NaN` — and null for a missing one, which nulls the code and drops the row. It reads oddly
because dftly has no null test; `if $x` will not type-check against a float column and a bare
`null` parses as the string `"null"`. Tracked in
[dftly#113](https://github.com/mmcdermott/dftly/issues/113); this becomes `if is_present($x)`
once that lands.

`NaN` and `inf` are deliberately preserved rather than dropped — they are recorded values, and
changing that is a data-modelling decision rather than a bug fix.

## Numeric-value guards

`numeric_value` is strict-cast downstream, so a String source column containing a single
non-numeric row aborts the stage. Which columns are String is not inferable from the demo — it
depends on what junk happens to appear — so the guarded set comes from the official eICU DDL
(`MIT-LCP/eicu-code`, `postgres_create_tables.sql`): `age`, `medication.dosage`, and
`infusiondrug.{infusionrate, patientweight}`. Numeric-typed columns are passed through bare,
since the schema forbids junk in them.

The guard regex is exactly Rust's `f64::from_str` grammar, so it is value-for-value identical to
polars' non-strict cast. A narrower hand-rolled pattern silently drops real values such as
`1e5`, `25.` and `+5`; `tests/test_numeric_guard.py` pins the equivalence by differential
fuzzing.

## Code metadata: descriptions

`metadata/codes.parquet` carries `code`, `description`, `parent_codes` and `code_template`. eICU
ships no code dictionary (nothing like MIMIC-IV's `d_labitems`): its tables record the names
themselves (`labName`, `drugName`, `treatmentString`, `diagnosisString`), so the codes are
human-readable on their own and releases up to 0.1.0 wrote no `description`. That is enough
for a reader, not for tooling that maps codes across datasets or builds features from
descriptions: vital codes are column names (`VITALS//PERIODIC//HEARTRATE`), lab names are
site picklist abbreviations (`Hgb`, `-polys`, `WBC x 1000`), and vocabulary-based enrichment
(OHDSI Athena) cannot resolve either. So every code now gets the documented meaning of what it
encodes, through three routes, with the source of each description recorded:

| Code family                                                                                                                                                                            | Route                                                                                                                                                                                               | Source of the text                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                        |
| -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `LAB//RESULT//…`, `LAB//SPECIMEN_DRAWN//…`                                                                                                                                             | `src/eICU_MEDS/metadata/lab_descriptions.csv`, joined by `_metadata` on the raw `labname` (a partial match, so one row covers every unit variant); pre-MEDS writes it as `lab_descriptions.parquet` | per row, in the table's `source` column: the WeavEHR concept the lab name maps to ([aidh-ms/WeavEHR](https://github.com/aidh-ms/WeavEHR) `configs/datasets/eicu-crd/2.0/mappings`, MIT, commit 210a539), the MIMIC-IV lab item mapped to the same concept and its LOINC code ([mimic-code](https://github.com/MIT-LCP/mimic-code) `d_labitems_to_loinc.csv`, MIT), the canonical column in [eicu-code](https://github.com/MIT-LCP/eicu-code) `pivoted-lab.sql` / `pivoted-bg.sql` (MIT), or, for the long tail, a plain expansion of the picklist name marked `expansion` |
| literal codes: the 26 `VITALS//…`, `ICU_ADMISSION_WEIGHT`/`_HEIGHT`, `ICU_DISCHARGE_WEIGHT`, `SUBJECT_WEIGHT_AT_INFUSION`, `INFUSION_DRUG`, `CAREPLAN_EOL`, `MEDS_BIRTH`, `MEDS_DEATH` | `src/eICU_MEDS/metadata/static_code_descriptions.csv`, filled into the finished `codes.parquet` by the ETL's last step (`code_descriptions.apply_static_descriptions`)                              | per row: the column description in the [eICU-CRD table documentation](https://eicu.mit.edu/eicutables/), the WeavEHR concept and unit, and the MIMIC-IV `d_items` label of the item WeavEHR maps to the same concept (open MIMIC-IV demo, ODbL); `expansion` where only the abbreviation is documented (ST1–3, SVR/SVRI, PVR/PVRI, cardiac input)                                                                                                                                                                                                                         |
| every other family (patient events, diagnoses, admission diagnoses, allergies, care plan items, medications, treatments)                                                               | `_metadata: _self: description:` templates in `configs/event_configs.yaml`, evaluated per event                                                                                                     | the code's own components, named after the eICU-CRD column they come from (`patient.unitAdmitSource`, `diagnosis.diagnosisString`, …)                                                                                                                                                                                                                                                                                                                                                                                                                                     |

Where a lab name is bridged to a MIMIC-IV item with a LOINC code, the lab result code also
gets `parent_codes: ["LOINC/<code>"]` (50 lab names), so Athena-based tooling can resolve it.
The bridge is an inference across two curated mappings (eICU name → WeavEHR concept ← MIMIC-IV
item), and the LOINC code inherits the MIMIC item's specimen (serum/plasma versus blood); the
`source` column records both ends of every bridge. Diagnoses keep their ICD parents (below).

Why three routes: MEDS-Extract attaches `_metadata` by joining on the code's *components*, so
a literal code has none to join on, and the pre-existing `metadata/codes.parquet` it documents
for that case is not read by the standard extraction pipeline (`extract_code_metadata` reads
its prior metadata stage's output, `split_and_shard_subjects`, which writes no
`codes.parquet`). The post-pipeline fill only ever writes a description where the pipeline
left null, never extends the vocabulary, and is idempotent.

On the demo (eICU-CRD demo 2.0.1) every one of the 6,481 codes is described: 294 lab codes
(all 158 lab names of the full release are in the table, so full-release builds are covered
too), 34 literal codes, and 6,153 component-coded codes. Event data is untouched: per-code
event counts are identical to a build without this metadata.

MEDS 0.4 makes `description` optional, so consumers must still not rely on it being
non-null for datasets built with older releases.

ICD parent codes for diagnoses are the other piece of code metadata: each `DIAGNOSIS//...`
code gets `ICD9CM/...` and/or `ICD10CM/...` parent codes from the `icd9code` column of
`diagnosis` (via the `dx_icd_map` table built in pre-MEDS). In the demo, 860 of the 1,091
diagnosis codes have one.

## Which eICU tables are extracted

eICU-CRD v2.0 ships 31 tables. This pipeline reads 15 of them. The rest are excluded
deliberately — the reasoning is recorded here rather than in a code comment, because
"this table is missing" is otherwise indistinguishable from an oversight.

**Extracted:** `patient`, `hospital` (joined for site attributes), `admissionDx`,
`allergy`, `carePlanEOL`, `carePlanGeneral`, `carePlanGoal`,
`carePlanInfectiousDisease`, `diagnosis`, `infusionDrug`, `lab`, `medication`,
`treatment`, `vitalAperiodic`, `vitalPeriodic`.

**Excluded — deliberate, on the merits:**

| Table                  |       Rows | Why not                                                                                                                                                                          |
| ---------------------- | ---------: | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `admissionDrug`        |    874,920 | [Documented](https://eicu.mit.edu/eicutables/admissiondrug/) as "extremely infrequently used".                                                                                   |
| `apacheApsVar`         |    171,177 | Inputs to the APACHE score. We prefer the raw measurements they were computed from.                                                                                              |
| `apachePatientResult`  |    297,064 | Pre-computed APACHE scores and predictions — derived values, not observations.                                                                                                   |
| `apachePredVar`        |    171,177 | Further APACHE inputs; same reasoning.                                                                                                                                           |
| `carePlanCareProvider` |    502,765 | Cannot be linked to the specific care-plan entries it describes, and its offsets record when a provider was entered into the plan rather than any clinical event.                |
| `customLab`            |      1,082 | Documentation is very sparse, and it holds only the lab measurements that could not be mapped to the standard set in `lab`.                                                      |
| `intakeOutput`         | 12,030,289 | The documentation carries significant warnings about duplicate and cumulative values. Excluded until those are handled properly rather than silently.                            |
| `microLab`             |     16,996 | **Time leakage**: culture-taken time is not culture-result time, so a model would see organism/sensitivity results before they could exist. Also documented as poorly populated. |
| `note`                 |  2,254,179 | Largely duplicative of the structured tables — narrative notes were mostly removed for PHI reasons.                                                                              |

**Excluded — not yet done, not on the merits.** These carry real clinical signal and
were stubbed out but never finished (the original config shipped commented-out blocks
for them marked `NOT YET DONE`, because their cell-label/value structure needs a code
scheme of its own):

| Table                 |        Rows | Contains                                                                                            |
| --------------------- | ----------: | --------------------------------------------------------------------------------------------------- |
| `nurseCharting`       | 151,604,232 | Nursing-charted observations, including vitals not in `vitalPeriodic`. By far the largest omission. |
| `respiratoryCharting` |  20,168,176 | Ventilator and respiratory observations.                                                            |
| `nurseAssessment`     |  15,602,498 | Structured nursing assessments.                                                                     |
| `physicalExam`        |   9,212,316 | Physical examination findings.                                                                      |
| `nurseCare`           |   8,311,132 | Nursing care entries.                                                                               |
| `pastHistory`         |   1,149,180 | Comorbidity / prior history, as a `/`-separated hierarchy.                                          |
| `respiratoryCare`     |     865,381 | Ventilator settings and airway management.                                                          |

## MEDS-transforms settings

If you want to convert a large dataset, you can use parallelization with MEDS-transforms
(the MEDS-transformation step that takes the longest).

Local parallelization uses the `hydra-joblib-launcher` package, which is installed as a dependency of
`eICU-MEDS`. You can set the number of workers as an environment variable:

```bash
export N_WORKERS=8
```

The number of subjects per shard is fixed at 1,000 in the `split_and_shard_subjects` stage of
`src/eICU_MEDS/configs/ETL.yaml`; no environment variable or command-line option changes it. Other MEDS ETLs
do the same (MIMIC-IV fixes it in its spec's `etl:` block): MEDS-Extract treats shard size as a property of
the dataset, while worker counts belong to the machine running it. To use a different shard size, edit that
file in a local checkout (see [Development](#development)).

## Development

Contributors should use [`uv`](https://docs.astral.sh/uv/) to set up a development environment from the
lockfile:

```bash
git clone https://github.com/Medical-Event-Data-Standard/eICU_MEDS.git
cd eICU_MEDS
uv sync --group dev
uv run pre-commit install
uv run pytest src/ tests/ --doctest-modules
```

The end-to-end test (`tests/e2e_demo_test.py`) downloads the public eICU demo from PhysioNet, so it is
slow on a first run.
