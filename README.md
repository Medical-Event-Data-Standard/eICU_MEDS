# eICU MEDS Extraction ETL

[![PyPI - Version](https://img.shields.io/pypi/v/eICU-MEDS)](https://pypi.org/project/eICU-MEDS/)
[![Documentation Status](https://readthedocs.org/projects/etl-meds/badge/?version=latest)](https://etl-meds.readthedocs.io/en/stable/?badge=stable)
![Static Badge](https://img.shields.io/badge/MEDS-0.4.1-blue)
[![codecov](https://codecov.io/gh/Medical-Event-Data-Standard/eICU_MEDS/graph/badge.svg?token=RW6JXHNT0W)](https://codecov.io/gh/Medical-Event-Data-Standard/eICU_MEDS)
[![tests](https://github.com/Medical-Event-Data-Standard/eICU_MEDS/actions/workflows/tests.yaml/badge.svg)](https://github.com/Medical-Event-Data-Standard/eICU_MEDS/actions/workflows/tests.yml)
[![code-quality](https://github.com/Medical-Event-Data-Standard/eICU_MEDS/actions/workflows/code-quality-main.yaml/badge.svg)](https://github.com/Medical-Event-Data-Standard/eICU_MEDS/actions/workflows/code-quality-main.yaml)
![python](https://img.shields.io/badge/python-%3E%3D3.11.4%2C%20%3C3.14-blue?logo=python&logoColor=white)
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
meds-extract-run spec=eICU output_dir=data/eicu_meds
```

The package registers its ETL with [MEDS-Extract](https://github.com/mmcdermott/MEDS_extract) as the
spec `eICU` ([`src/eICU_MEDS/configs/event_configs.yaml`](src/eICU_MEDS/configs/event_configs.yaml)),
so `meds-extract-run` runs all of it. When you run this, it will:

1. Download the raw eICU-CRD tables this ETL reads (see
    [Which eICU tables are extracted](#which-eicu-tables-are-extracted)) for the currently supported
    version into `data/eicu_meds/.meds_extract_run/raw_input` (`download_dest_dir=` puts them
    elsewhere). Files that already exist and verify against PhysioNet's `SHA256SUMS.txt` are skipped,
    so an interrupted run resumes rather than restarting.
2. Write the two tables the config reads that the release does not have (the ICD codes of each
    diagnosis path and the packaged lab descriptions) into `data/eicu_meds/eicu_pre_meds`, with this
    package's `eicu_pre_meds` stage. The raw directory is never modified.
3. Construct the MEDS cohort from the raw tables — every transformation, join, pseudotime derivation
    and code description is declared in the spec — and write it to `data/eicu_meds/data` and
    `data/eicu_meds/metadata`.
4. Sort each code's ICD parent codes (`eicu_sort_parent_codes`).
5. Enrich `metadata/codes.parquet` in place with [suMEDS](https://github.com/rvandewater/SumMEDS)
    (installed with this package): LOINC parents for lab codes and parents among eICU's own codes
    (see [Enriching code metadata with suMEDS](#enriching-code-metadata-with-sumeds)). A failed
    enrichment (e.g. without internet) does not fail the run, and `metadata/dataset.json` records
    whether the build was enriched.

The public [demo](https://physionet.org/content/eicu-crd-demo/2.0.1/) needs no
credentials:

```bash
meds-extract-run spec=eICU output_dir=data/eicu_demo dataset_key=demo
```

To build from raw tables you already have, skip the download (add `dataset_key=demo` for a copy of
the demo, so that `metadata/dataset.json` records the demo's version):

```bash
meds-extract-run spec=eICU output_dir=$MEDS_OUTPUT_DIR do_download=false input_dir=$RAW_INPUT_DIR
```

PhysioNet throttles each connection to about 50 KB/s but not the total, so `download_concurrency=8`
(default 4) makes the download of the full release about twice as fast.

Each stage keeps its intermediate output under `output_dir`, so a failed run resumes where it stopped;
`do_cleanup_intermediates=true` deletes them once the run has succeeded. See the
[MEDS-Extract README](https://github.com/mmcdermott/MEDS_extract#running-a-packaged-dataset-etl) for
the other options of `meds-extract-run`.

### Migrating from `MEDS_extract-eICU`

Releases up to 0.2.0 shipped their own runner, `MEDS_extract-eICU`. It is gone; `meds-extract-run`
builds the same events and code metadata (the literal codes and lab names outside the lab table now
also carry a `code_template`). Its options map as follows:

| `MEDS_extract-eICU`                                   | `meds-extract-run spec=eICU`                                                        |
| ----------------------------------------------------- | ----------------------------------------------------------------------------------- |
| `root_output_dir=ROOT` (cohort in `ROOT/MEDS_output`) | `output_dir=OUT` (cohort in `OUT/data`, `OUT/metadata`)                             |
| `raw_input_dir=RAW` (download destination)            | `download_dest_dir=RAW`                                                             |
| `raw_input_dir=RAW do_download=False`                 | `input_dir=RAW do_download=false`                                                   |
| `do_demo=True`                                        | `dataset_key=demo`                                                                  |
| `download_workers=8`                                  | `download_concurrency=8`                                                            |
| `do_enrich=false`                                     | `skip_stages=[sumeds_enrich]`                                                       |
| `'enrich_args=[--strict]'`                            | `"overrides=['++stage_cfg.strict=true']"` (see [below](#stage-options-at-run-time)) |
| `N_WORKERS=8`                                         | `stage_runner_fp=runner.yaml` (see [Parallel runs](#parallel-runs))                 |

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
output are not real and must not be interpreted as such: because every stay ends on December 31,
the date of an event reveals how long until hospital discharge. See
[docs/data_leakage.md](docs/data_leakage.md) for this and every other way the output can leak
information, table by table.

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
(`MIT-LCP/eicu-code`, `postgres_create_tables.sql`): `age`, `medication.dosage`,
`infusiondrug.{infusionrate, patientweight}`, `nurseCharting.nursingChartValue`,
`respiratoryCharting.respChartValue`, `nurseAssessment.cellAttributeValue`,
`nurseCare.cellAttributeValue` and `physicalExam.physicalExamText`.
Numeric-typed columns are passed through bare, since the schema forbids junk in them.

Where the non-numeric remainder is worth keeping (charted values, below), the same pattern also
appears negated as a regex match, `$x if not (/<guard>/ in $x)`, to fill `text_value`, so each
event carries exactly one of the two values.

The guard regex is exactly Rust's `f64::from_str` grammar, so it is value-for-value identical to
polars' non-strict cast. A narrower hand-rolled pattern silently drops real values such as
`1e5`, `25.` and `+5`; `tests/test_numeric_guard.py` pins the equivalence by differential
fuzzing.

## Charted observations

Several tables record each observation as one varchar cell under picklist labels or a label
path. Each row becomes one event whose code is the table's prefix followed by those labels:

| Table                 | Code                                          | Time                      | Full-release rows | Blank (no event) | Entry = chart offset | Entry lag, 90th percentile |
| --------------------- | --------------------------------------------- | ------------------------- | ----------------: | ---------------: | -------------------: | -------------------------: |
| `nurseCharting`       | `NURSE_CHARTING//<category>//<label>//<name>` | `nursingChartEntryOffset` |       151,604,232 |          284,600 |                  73% |                     57 min |
| `respiratoryCharting` | `RESPIRATORY_CHARTING//<category>//<label>`   | `respChartEntryOffset`    |        20,168,176 |                3 |                  75% |                     47 min |
| `nurseAssessment`     | `NURSE_ASSESSMENT//<cellAttributePath>`       | `nurseAssessEntryOffset`  |        15,602,498 |                0 |                   5% |                    195 min |
| `nurseCare`           | `NURSE_CARE//<cellAttributePath>`             | `nurseCareEntryOffset`    |         8,311,132 |                0 |                   5% |                    206 min |

- A value the numeric guard accepts goes to `numeric_value`. Anything else, such as GCS descriptors
    (`4-->(E4) spontaneous`), `Oral`, `WDL` or `Nasal cannula`, goes to `text_value`. A value written
    as a percentage (`35%`) is text: stripping the `%` would need a second guard pattern.
- A blank cell emits no event.
- The time is the entry offset, not the charted offset, so a value is not visible before it was
    recorded. The last two columns show how far apart the two are.
- The category is part of the code because the same label can sit under more than one category:
    `PA` under both `Invasive` and `Vital Signs` in `nurseCharting` (96 codes from 83 label/name
    pairs on the full release), and 198 codes from 194 labels in `respiratoryCharting`.
- In the path-coded tables the path ends in the attribute (`…|Neurologic|Pupils|Right`) and
    determines the label, so the path alone is the code. They cover few stays: `nurseAssessment` has
    rows for 13,001 and `nurseCare` for 13,052 of the 200,859 unit stays on the full release.
- Vitals charted here overlap with `vitalPeriodic`/`vitalAperiodic`. They keep their own
    source-prefixed codes, and nothing is deduplicated across tables.
- The two charted tables overlap in one measurement: on the full release, 79% of
    `respiratoryCharting`'s `RR (patient)` values repeat `nurseCharting`'s `Respiratory Rate` for the
    same stay at the same minute. Ventilator settings and measurements (PEEP, tidal volume, pressure
    support, plateau pressure, compliance) appear only in `respiratoryCharting`.

## Intake and output

`intakeOutput` is the nursing intake/output flowsheet. Each row becomes one
`INTAKE_OUTPUT//<cellPath>` event, for example
`INTAKE_OUTPUT//flowsheet|Flowsheet Cell Labels|I&O|Output (ml)|Urine`, valued by the row's own
`cellValueNumeric` and timed at `intakeOutputEntryOffset`, as for the charted tables. The path ends
in the row's label, so the label adds nothing to the code.

- `intakeTotal`, `outputTotal`, `dialysisTotal` and `netTotal` are not emitted. The
    [documentation](https://github.com/MIT-LCP/eicu-code/blob/main/website/content/eicutables/intakeOutput.md)
    describes them as cumulative up to the row's offset and repeated across rows entered at the
    same time, so emitting them would count the same fluid many times.
- `cellValueText` is not emitted. It is the same number written as text.
- Rows are not deduplicated. On the full release 9,619 rows repeat another row in everything but
    their ID, and the table gives no way to tell a repeated entry from two equal ones.
- The documentation says `cellValueNumeric` is always positive. 1,476 full-release rows are
    negative, and they are emitted as recorded.

## Physical exam

`physicalExam` holds the structured physical exam entered in eCareManager. Each row becomes one
`PHYSICAL_EXAM//<physicalExamPath>` event, for example
`PHYSICAL_EXAM//notes/Progress Notes/Physical Exam/Physical Exam/Neurologic/GCS/Motor Score/6`,
timed at `physicalExamOffset`, the table's only offset, which the documentation describes as the
time the item was entered.

- The path ends in the picklist choice (`physicalExamValue`), so the choice is already in the code.
- `physicalExamText` is split like the charted values. For 63 vital-sign, weight and I&O items
    (`HR Current`, `Weight (kg)/Admission`, …) it is the measurement and goes to `numeric_value`;
    for the other items it mostly repeats the choice and goes to `text_value`.
- A blank text does not drop the event, because the path is the finding. On the full release 41,327
    rows have a blank text, all in items that are not measurements, and their events carry no value.
- The vital signs here are the exam's summaries (current, lowest and highest over 24 hours). Like
    the charted vitals, they keep their own codes and are not deduplicated against `vitalPeriodic`.

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

| Code family                                                                                                                                                                                                                                  | Route                                                                                                                                                                                                                                                | Source of the text                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                  |
| -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `LAB//RESULT//…`, `LAB//SPECIMEN_DRAWN//…`                                                                                                                                                                                                   | `src/eICU_MEDS/metadata/lab_descriptions.csv`, which the `eicu_pre_meds` stage writes as `lab_descriptions.parquet`; the `lab` table joins it on the raw `labname` (one row covers every unit variant), and a `_self` template reads its description | per row, in the table's `source` column: the WeavEHR concept the lab name maps to ([aidh-ms/WeavEHR](https://github.com/aidh-ms/WeavEHR) `configs/datasets/eicu-crd/2.0/mappings`, MIT, commit 210a539), the label of the MIMIC-IV lab item mapped to the same concept ([mimic-code](https://github.com/MIT-LCP/mimic-code) `d_labitems_to_loinc.csv`, MIT), the canonical column in [eicu-code](https://github.com/MIT-LCP/eicu-code) `pivoted-lab.sql` / `pivoted-bg.sql` (MIT), or, for the long tail, a plain expansion of the picklist name marked `expansion` |
| literal codes: the 26 `VITALS//…`, `ICU_ADMISSION_WEIGHT`/`_HEIGHT`, `ICU_DISCHARGE_WEIGHT`, `SUBJECT_WEIGHT_AT_INFUSION`, `INFUSION_DRUG`, `CAREPLAN_EOL`, `MEDS_BIRTH`, `MEDS_DEATH`                                                       | a constant `_self` description in `configs/event_configs.yaml`, with the source of the text in a `# Source:` comment above it                                                                                                                        | per row: the column description in the [eICU-CRD table documentation](https://eicu.mit.edu/eicutables/), the WeavEHR concept and unit, and the MIMIC-IV `d_items` label of the item WeavEHR maps to the same concept (open MIMIC-IV demo, ODbL); `expansion` where only the abbreviation is documented (ST1–3, SVR/SVRI, PVR/PVRI, cardiac input)                                                                                                                                                                                                                   |
| every other family (patient events, diagnoses, admission diagnoses, allergies, care plan items, intake/output, medications, nursing assessment, nursing care, nurse charting, past history, physical exam, respiratory charting, treatments) | `_metadata: _self: description:` templates in `configs/event_configs.yaml`, evaluated per event                                                                                                                                                      | the code's own components, named after the eICU-CRD column they come from (`patient.unitAdmitSource`, `diagnosis.diagnosisString`, …)                                                                                                                                                                                                                                                                                                                                                                                                                               |

The lab table carries descriptions only. eICU records no LOINC code for its lab names, so the
ETL writes no LOINC parent for them; the lab-name to LOINC crosswalk is part of suMEDS's
versioned ontology data and is applied after extraction (see [Enriching code metadata with suMEDS](#enriching-code-metadata-with-sumeds)).
Diagnoses keep their ICD parents (below), which come from eICU's own `icd9code` column.

The lab table lists only lab names that are already public: the 147 in the open eICU-CRD demo
(ODbL) and one more in [eicu-code](https://github.com/MIT-LCP/eicu-code) (MIT). It ships nothing
that exists only in the credentialed release. A lab name outside the table (or a blank one) gets a
generic description from the same template, for example `Lab result (eICU lab.labName '<labname>')`.

Every description is written by MEDS-Extract from the spec. Releases up to 0.2.0 filled in the
literal codes and the lab names outside the table after the pipeline, because MEDS-Extract could not
attach metadata to a code without components before 0.8.1; the text is the same.

On the demo (eICU-CRD demo 2.0.1) every one of the 8,122 codes is described: 294 lab codes,
34 literal codes, and 7,794 component-coded codes. Event data is untouched: per-code event
counts are identical to a build without this metadata.

On the full release (eICU-CRD 2.0) every one of the 15,528 codes is described: 316 lab codes
(a `RESULT` and a `SPECIMEN_DRAWN` code for each of the 158 lab names; 148 names are in the
table and the other 10 get the generic description), 34 literal codes, and 15,178
component-coded codes. Every code in the data is in `codes.parquet`, and the file validates
against `meds.CodeMetadataSchema`.

MEDS 0.4 makes `description` optional, so consumers must still not rely on it being
non-null for datasets built with older releases.

ICD parent codes for diagnoses are the other piece of code metadata: each `DIAGNOSIS//...`
code gets `ICD9CM/...` and/or `ICD10CM/...` parent codes from the `icd9code` column of
`diagnosis`. That column lists several codes in one cell, and the config cannot split a cell into
several metadata rows yet ([dftly#87](https://github.com/mmcdermott/dftly/issues/87)), so the
`eicu_pre_meds` stage does that one step (the `dx_icd_map` table) and the config decides each code's
vocabulary. In the demo, 860 of the 1,091 diagnosis codes have one; in the full release, 3,129 of the
3,933. The `eicu_sort_parent_codes` stage sorts them, so that they read the same way across codes and
releases; the suMEDS enrichment appends its parents after them.

## Enriching code metadata with suMEDS

The ETL writes what eICU itself records. Links to external vocabularies that eICU does not
record, and parents among eICU's own codes, are added by
[suMEDS](https://github.com/rvandewater/SumMEDS). The package ships the rules for it,
[`src/eICU_MEDS/sumeds_rules.yaml`](src/eICU_MEDS/sumeds_rules.yaml), registered in suMEDS's
`sumeds.rules` entry-point group under the dataset's name, `eICU`, so `--rules eICU` works for any
suMEDS command. The spec runs suMEDS's `sumeds_enrich` stage with them as the last stage of
`meds-extract-run spec=eICU`:

```yaml
etl:
  post_stages:
    - eicu_sort_parent_codes
    - sumeds_enrich:
        rules: eICU
```

It replaces `metadata/codes.parquet` only once the enriched table is complete, and the file stays
valid MEDS code metadata. The crosswalk tables and ontology packs come from suMEDS's versioned
ontology data (sumeds-ontologies, pinned release), fetched from GitHub on first use and cached under
`~/.cache/sumeds`, so this repository does not ship them, and no Athena download or LOINC account is
needed.

### Was a build enriched?

Enrichment is on by default and soft-fails: if it fails, for example on a server without internet
access, `metadata/codes.parquet` stays as the ETL wrote it and the run still succeeds. The warning,
with the command that finishes the enrichment, is in `OUTPUT_DIR/.logs/pipeline.log`, not on the
console. `metadata/dataset.json` records the outcome under `sumeds_enrichment.enriched`: `true`, or
`false` with the `error` and the `retry` command:

```bash
python -c 'import json, sys; print(json.load(open(sys.argv[1]))["sumeds_enrichment"]["enriched"])' \
    "$OUT/metadata/dataset.json"
```

Running the same `meds-extract-run` command again does **not** retry: the stage counts as done even
when the enrichment soft-failed. Finish the enrichment with `suMEDS-enrich`, which uses the rules and options recorded
in `dataset.json` and updates the record to match a successful run:

```bash
suMEDS-enrich "$OUT"
# Without internet, from a copy of the sumeds-ontologies packs directory:
suMEDS-enrich "$OUT" --ontology-source /path/to/sumeds-ontologies/ontologies --offline
```

### Leaving it out

```bash
meds-extract-run spec=eICU output_dir=$OUT skip_stages=[sumeds_enrich]
```

writes the ETL output alone, with no `sumeds_enrichment` record. Running the same command later
without `skip_stages` runs only the enrichment.

### Stage options at run time

The stage takes the `suMEDS-enrich` options, spelled with underscores (`strict`, `offline`,
`ontology_source`, `athena_csv`, …; see the
[suMEDS usage docs](https://github.com/rvandewater/SumMEDS/blob/main/docs/usage.md)). Set them for one
run with `overrides=`, which `meds-extract-run` passes to every stage. The `++` adds the option to the
stage config of the stages that do not have it, which ignore it:

```bash
# Fail the run if the enrichment fails:
meds-extract-run spec=eICU output_dir=$OUT "overrides=['++stage_cfg.strict=true']"
# Also look codes up in your own OHDSI Athena download:
meds-extract-run spec=eICU output_dir=$OUT "overrides=['++stage_cfg.athena_csv=/path/to/athena']"
# Use only cached ontology data, without trying the network:
meds-extract-run spec=eICU output_dir=$OUT "overrides=['++stage_cfg.offline=true']"
# Read the ontology packs from a local copy (or set SUMEDS_ONTOLOGY_SOURCE=DIR):
meds-extract-run spec=eICU output_dir=$OUT "overrides=['++stage_cfg.ontology_source=/path/to/packs']"
```

Without the `++` (`overrides=['stage_cfg.strict=true']`), the first stage rejects the unknown key
and the run fails.

### Clusters without internet access on compute nodes

Fill a cache on shared storage once, from a login node, then build offline on the compute nodes with
the same cache:

```bash
export XDG_CACHE_HOME=/shared/project/cache
suMEDS-enrich --prefetch --rules eICU   # login node: the whole pinned ontology release, a few tens of MB
meds-extract-run spec=eICU output_dir=$OUT do_download=false input_dir=$RAW \
    "overrides=['++stage_cfg.offline=true']"   # compute node
```

### What the rules add

- **LOINC for lab results** (`eicu-lab-loinc`, a crosswalk): `LAB//RESULT//{labname}//{unit}`
    gets `LOINC//<code>` in `parent_codes`, plus `vocabulary_id` and `concept_code`, for the 50
    lab names in the crosswalk table `eicu/lab_loinc`, each validated against the LOINC
    pack. The links are an inference across two curated mappings (eICU name → WeavEHR concept ←
    MIMIC-IV item with its LOINC code in mimic-code `d_labitems_to_loinc.csv`), so the LOINC code
    inherits the MIMIC item's specimen (serum/plasma versus blood); the table records both ends.
- **Parents among eICU codes** (`eicu-diagnosis-paths`, `eicu-admission-diagnosis-paths`,
    `eicu-treatment-paths`, hierarchy rules): a `|`-separated path gets the nearest shorter path
    that is itself a code, e.g. `TREATMENT//ENTERED//a|b|c` gets `TREATMENT//ENTERED//a|b`. On
    the full release that adds 2,929 diagnosis and 2,096 treatment parents; admission diagnosis
    paths are recorded only as leaves, so that rule adds none today.

suMEDS also looks up every `ICD9CM/…` and `ICD10CM/…` parent the ETL writes for a diagnosis code
in its ICD-9-CM and ICD-10-CM packs, and appends that code's ancestors.

The ETL alone emits no LOINC parents for lab codes (builds of `main` after #27 wrote
`LOINC/<code>` for the same 50 lab names), and it never emitted parents among eICU codes:
`metadata/codes.parquet` from the ETL has ICD parents for diagnoses only, and suMEDS supplies
the rest. It never removes or rewrites the ETL's own descriptions or parents: those stay first
in `parent_codes`, and what suMEDS adds is appended after them. `parent_names` maps each parent
code to its name (a `map<string, string>` column; same-table parents have no name, since their
description is in their own row).

## Which eICU tables are extracted

eICU-CRD v2.0 ships 31 tables. This pipeline reads 22 of them. The rest are excluded
deliberately — the reasoning is recorded here rather than in a code comment, because
"this table is missing" is otherwise indistinguishable from an oversight.

**Extracted:** `patient`, `hospital` (joined for site attributes), `admissionDx`,
`allergy`, `carePlanEOL`, `carePlanGeneral`, `carePlanGoal`,
`carePlanInfectiousDisease`, `diagnosis`, `infusionDrug`, `intakeOutput`, `lab`, `medication`,
`nurseAssessment`, `nurseCare`, `nurseCharting`, `pastHistory`, `physicalExam`, `respiratoryCharting`, `treatment`, `vitalAperiodic`,
`vitalPeriodic`.

**Excluded — deliberate, on the merits:**

| Table                  |      Rows | Why not                                                                                                                                                                          |
| ---------------------- | --------: | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `admissionDrug`        |   874,920 | [Documented](https://eicu.mit.edu/eicutables/admissiondrug/) as "extremely infrequently used".                                                                                   |
| `apacheApsVar`         |   171,177 | Inputs to the APACHE score. We prefer the raw measurements they were computed from.                                                                                              |
| `apachePatientResult`  |   297,064 | Pre-computed APACHE scores and predictions — derived values, not observations.                                                                                                   |
| `apachePredVar`        |   171,177 | Further APACHE inputs; same reasoning.                                                                                                                                           |
| `carePlanCareProvider` |   502,765 | Cannot be linked to the specific care-plan entries it describes, and its offsets record when a provider was entered into the plan rather than any clinical event.                |
| `customLab`            |     1,082 | Documentation is very sparse, and it holds only the lab measurements that could not be mapped to the standard set in `lab`.                                                      |
| `microLab`             |    16,996 | **Time leakage**: culture-taken time is not culture-result time, so a model would see organism/sensitivity results before they could exist. Also documented as poorly populated. |
| `note`                 | 2,254,179 | Largely duplicative of the structured tables — narrative notes were mostly removed for PHI reasons.                                                                              |

**Excluded — not yet done, not on the merits.** These carry real clinical signal and
were stubbed out but never finished (the original config shipped commented-out blocks
for them marked `NOT YET DONE`, because their cell-label/value structure needs a code
scheme of its own):

| Table             |    Rows | Contains                                   |
| ----------------- | ------: | ------------------------------------------ |
| `respiratoryCare` | 865,381 | Ventilator settings and airway management. |

## Parallel runs

`meds-extract-run` runs every stage in one process by default. To run the data stages with several
workers, give it a runner file with a `parallelize` block (the joblib launcher is installed with this
package; `pip install 'eICU-MEDS[slurm_parallelism]'` adds the submitit launcher for Slurm):

```bash
printf 'parallelize:\n  n_workers: 8\n  launcher: joblib\n' > runner.yaml
meds-extract-run spec=eICU output_dir=$OUT stage_runner_fp=runner.yaml
```

Size `n_workers` to memory, not cores: each worker is a separate process with its own working
set. On the full release, four workers exhausted a 24 GB laptop, with each of the four
holding 7.5 to 8 GB during `split_and_shard_subjects`. That is a metadata stage, so every worker
repeats the whole computation rather than taking a share of it. The same build run serially
(polars limited to four threads with `POLARS_MAX_THREADS=4`) peaked at 4.8 GB and took 20 minutes on
a 10-core machine. This package's own stages and the suMEDS enrichment work in worker 0 only.

The number of subjects per shard is fixed at 1,000 in the spec's `etl:` block
(`n_subjects_per_shard`); no command-line option changes it. MEDS-Extract treats shard size as a
property of the dataset, while worker counts belong to the machine running it. To use a different
shard size, edit the spec in a local checkout (see [Development](#development)).

## Summary statistics

[`tests/regression/full_summary_stats.json`](tests/regression/full_summary_stats.json) is a
[meds_summary_stats](https://github.com/Medical-Event-Data-Standard/meds_summary_stats) fingerprint of
the MEDS dataset this ETL builds from the full eICU-CRD 2.0 release with `meds-extract-run spec=eICU`,
so its code-metadata coverage includes the suMEDS enrichment. It covers subject, event and measurement
counts, digests of the code vocabulary, per-subject, value and time distributions, code-metadata
coverage, the splits and the schema. It names no code and reports no cell of fewer than 20 subjects and
no minimum or maximum, which is what makes it safe to publish. Because it is committed next to the ETL,
its diff shows what an ETL change did to the output, and two ETL versions can be compared without
access to the data.

[`tests/regression/demo_summary_stats.json`](tests/regression/demo_summary_stats.json) is the same
fingerprint of the open eICU-CRD demo. The Tests workflow builds the demo on every pull request and
fails when the result no longer matches it (`scripts/summary_stats.sh demo` regenerates it; the job
also uploads the new statistics as the `eicu-demo-summary-stats` artifact).

Regenerate it in every pull request that changes the output, from a build of that branch:

```bash
# Build the release with this checkout, then summarize it
scripts/summary_stats.sh full --input-dir $RAW_INPUT_DIR
# Or summarize a build of this checkout you already have
scripts/summary_stats.sh full --meds-dir $MEDS_OUTPUT_DIR
```

The script builds with `strict` enrichment and refuses a build whose `metadata/dataset.json` does not
record a successful enrichment, since the baselines describe enriched builds. Both print how the
statistics changed against the committed baseline before overwriting it. Add
`--compare` to check a build without writing anything; it exits 1 when
[`tests/regression/policy.yaml`](tests/regression/policy.yaml) flags an error. Rebuilding the same
commit reproduces the baseline exactly, so any finding is a change in the output. CI cannot rebuild the
credentialed release, so the [Summary stats workflow](.github/workflows/summary-stats.yaml) only warns
when a pull request changes `src/` (the config or the suMEDS rules) without regenerating the baseline.
A MEDS-Extract, suMEDS or polars upgrade can also move the output, so check it with `--compare`.

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
slow on a first run. `tests/test_run_with_enrichment.py` runs the ETL and the suMEDS enrichment
offline, on a few synthetic rows and a tiny local ontology source. [DEVELOPMENT.md](DEVELOPMENT.md) covers testing, running the ETL on the full release,
and cutting a release with `uv version --bump`.

## Citation

If you use this code in your research, please cite the following:

```bibtex
@article{PhysioNet-eicu-crd-2.0,
  author = {Pollard, Tom and Johnson, Alistair and Raffa, Jesse and Celi, Leo Anthony and Badawi, Omar and Mark, Roger},
  title = {{eICU Collaborative Research Database}},
  journal = {{PhysioNet}},
  year = {2019},
  month = apr,
  note = {Version 2.0},
  doi = {10.13026/C2WM1R},
  url = {https://doi.org/10.13026/C2WM1R}
}
@article{Pollard2018-ln,
  title     = "The {eICU} Collaborative Research Database, a freely available
               multi-center database for critical care research",
  author    = "Pollard, Tom J and Johnson, Alistair E W and Raffa, Jesse D and
               Celi, Leo A and Mark, Roger G and Badawi, Omar",
  journal   = "Sci. Data",
  publisher = "Springer Science and Business Media LLC",
  volume    =  5,
  number    =  1,
  pages     = "180178",
  month     =  sep,
  year      =  2018,
  copyright = "https://creativecommons.org/licenses/by/4.0",
  language  = "en"
}

@software{McDermott_eICU_MEDS_eICU_MEDS,
author = {McDermott, Matthew and Zhu, Mingcheng and van de Water, Robin Philippus},
license = {MIT},
title = {{eICU\_MEDS: eICU MEDS Extraction ETL}},
url = {https://github.com/Medical-Event-Data-Standard/eICU_MEDS/}
}
```
