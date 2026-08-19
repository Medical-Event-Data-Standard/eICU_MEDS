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
pip install eICU-MEDS # use `pip install -e .` for local installation in editing mode
export DATASET_DOWNLOAD_USERNAME=$PHYSIONET_USERNAME
export DATASET_DOWNLOAD_PASSWORD=$PHYSIONET_PASSWORD
MEDS_extract-eICU root_output_dir=data/eicu_meds
```

When you run this, the program will:

1. Download the raw eICU-CRD files for the currently supported version into
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

You can also point the two directories somewhere else directly:

```bash
MEDS_extract-eICU raw_input_dir=$RAW_INPUT_DIR MEDS_output_dir=$MEDS_OUTPUT_DIR
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

Using local parallelization with the `hydra-joblib-launcher` package, you can set the number of workers:

```
pip install hydra-joblib-launcher --upgrade
```

Then, you can set the number of workers as environment variable:

```bash
export N_WORKERS=8
```

Moreover, you can set the number of subjects per shard to balance the parallelization overhead based on how many
subjects you have in your dataset:

```bash
export N_SUBJECTS_PER_SHARD=100000
```
