# Data and time leakage

This document records where the eICU MEDS output can leak information a model should not have,
what the pipeline does about each case, and what is left for downstream users to handle. It
covers every table the pipeline reads and the pipeline-wide mechanics around them: the time
anchor, subjects and splits, and metadata.

All counts are aggregates over the credentialed eICU-CRD 2.0 release unless marked as from the
open demo (eICU-CRD demo 2.0.1). Example codes and values come from the demo or the official
[table documentation](https://github.com/MIT-LCP/eicu-code/tree/main/website/content/eicutables).

## What counts as leakage here

| Kind        | What goes wrong                                                                         | Example                                                                            |
| ----------- | --------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------- |
| Temporal    | An event is timestamped before its content could have been in the record.               | A nurse charts a value for 10:00 and enters it at 11:00, and the event says 10:00. |
| Outcome     | An event states the outcome, or a fact that was only settled later, at an earlier time. | "Active upon discharge" emitted at the time an item was entered.                   |
| Revision    | A value corrected later shows up at the time of the original entry.                     | A goal entered as Active, later edited to Resolved, appears as Resolved at entry.  |
| Anchoring   | The absolute timestamp, not the event, carries future information.                      | Dates that reveal how long until discharge (see below).                            |
| Identity    | The same patient is in both the training and the evaluation data.                       | One patient's two hospital stays are split across train and held-out.              |
| Label proxy | A code is legitimately available, but it is the thing a task's label is computed from.  | Urine output events used as inputs to an AKI model whose label is urine output.    |

### The rule the pipeline follows

**An event is timed at the earliest moment its whole content was in the eICU record.** When a table
records both when something happened and when it was entered (`…Offset` and `…EntryOffset` or
`…EnteredOffset`), the event uses the entered time. A value is then never visible before someone
recorded it, at the cost of placing it later than the physiology it describes.

## Pipeline-wide issues

### Absolute timestamps encode the time until hospital discharge

eICU has no dates, only minute offsets from unit admission (see
[A note on time in eICU](../README.md#a-note-on-time-in-eicu)). Every stay is anchored so that its
hospital discharge falls on **December 31 of the discharge year, at the recorded discharge clock
time**; all 2,174 hospital discharges on the demo land on December 31. So for any event,
`December 31 at the discharge clock time − event time` is exactly the time left until hospital
discharge.

- **Risk:** any feature derived from the calendar date (day of year, month, week, days to year end,
    absolute-time embeddings) leaks the remaining hospital length of stay, which correlates with
    mortality and most other outcomes.
- **Not affected:** time of day is real, because the anchor uses the real discharge clock time and
    offsets are exact minutes. Age is computed relative to `MEDS_BIRTH`, so it is fine as well.
- **What to do now:** use only relative time within a subject, and never use calendar features.
- **Possible fix (not implemented):** anchor on admission instead, for example at a fixed date and
    the recorded unit admission clock time, so that dates carry nothing about the discharge.

### The same patient can be several subjects

The MEDS `subject_id` is the health-system stay (`patienthealthsystemstayid`), because eICU orders
stays of the same patient only by year. The full release has 139,367 patients and 166,355
health-system stays: **18,881 patients have more than one stay, covering 45,869 subjects (27.6%)**.

- **Risk:** `metadata/subject_splits.parquet` assigns subjects at random, so one patient's stays can
    land in both the training and the held-out split. A model can then recognize a patient rather
    than generalize.
- **Not mitigated:** `uniquepid` is not emitted, so the output alone cannot be regrouped by patient.
    Emitting it, or splitting by patient during extraction, would close this.

### Outcomes the data states directly

These are correct at their own time, so a task that cuts its inputs at the prediction time never
sees them early. They are listed because a task that reads "everything in the stay" or sets its
prediction time at discharge will.

- `HOSPITAL_DISCHARGE//<status>//<location>` and `ICU_DISCHARGE//<status>//<location>` carry the
    discharge status (`Alive`/`Expired`) and destination, at discharge.
- `MEDS_DEATH` is placed at hospital discharge when the status is `Expired` (see
    [Death](../README.md#death)).
- Columns that describe the end of a stay are never emitted: `activeUponDischarge` (care plan,
    diagnosis and treatment tables), and the `actual*` outcomes in `apachePatientResult`.

### Codes that are the label

Several codes are legitimate inputs before the prediction time but are also what common labels are
computed from. A task must leave them out of its inputs, or close its observation window before the
label period starts.

| Task                      | Codes that define or nearly define the label                                                                                                  |
| ------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------- |
| Mortality, end of life    | `CAREPLAN_EOL`, `CAREPLAN_GENERAL//Care Limitation//…` (`Do not resuscitate`, `Comfort measures only`), `HOSPITAL_DISCHARGE//Expired//…`      |
| Acute kidney injury       | `INTAKE_OUTPUT//…\|Output (ml)\|Urine…` (KDIGO urine criterion), `LAB//RESULT//creatinine//…`                                                 |
| Renal replacement therapy | `INTAKE_OUTPUT//…\|Dialysis (ml)\|…`, dialysis treatments and care-plan items                                                                 |
| Mechanical ventilation    | `RESPIRATORY_CHARTING//…` settings and `RT Vent On/Off`, `NURSE_CHARTING//…//O2 Admin Device//…` (`ventilator`), `NURSE_CARE//…\|Airway Type` |
| Neurological decline      | GCS in `NURSE_CHARTING//Scores//Glasgow coma score//…`, `PHYSICAL_EXAM//…/GCS/…`                                                              |
| Delirium                  | `NURSE_CHARTING//Scores//Delirium Scale/Score//…`                                                                                             |

### Events after the stay ends

Late entries can be timed after hospital discharge. On the demo, 14.9% of medication events fall
after hospital discharge (mostly `MEDICATION//STOPPED` at a planned stop time), and under 1.5% of
every other code family (1.0% for `NURSE_CARE`, 0.06% for vitals). Inputs cut at the prediction
time exclude them. Labels that look for "any event after X" should not count them as care received.

### Other pipeline-wide points

- **Timeless events.** A row whose time expression evaluates to null is dropped, not emitted as a
    static code; only `GENDER//…` and `RACE//…` have no time (verified on the demo). A missing
    offset therefore cannot turn an event into something visible for the whole record.
- **Metadata over all subjects.** `codes.parquet`, the ICD parent map built in pre-MEDS, and the code
    descriptions are computed over every subject, held-out ones included. They are deterministic
    lookups with no counts or statistics, so they carry no outcome information, but a downstream
    vocabulary or normalization should still be fitted on the training split only.
- **Corrections.** eICU stores the final value of most rows. A value corrected later appears at the
    time of the original entry, and the data gives no way to detect it. Labs are the exception
    (`labResultRevisedOffset`), and care-plan rows behave like a history (below).
- **The same measurement more than once.** Vital signs and GCS can arrive from `vitalPeriodic`,
    `nurseCharting` and `physicalExam`, each at its own time. That isn't leakage, but the same value
    is counted more than once and appears again later.
- **Site fingerprints.** `nurseAssessment` and `nurseCare` cover about 13,000 of 200,859 unit stays,
    from a few hospitals. Their mere presence identifies the site, which matters for evaluation
    across hospitals; `HOSPITAL_ADMISSION//…` already carries region, teaching status and bed count.
- **Row identifiers.** Events carry surrogate keys (`nurse_chart_id`, `medication_id`, …). Standard
    MEDS tooling ignores extra columns, but a model that used them as features could pick up ordering.

## Table by table

"Entered later" is the share of rows whose entry time is after the time the value refers to, and
the lag column is the 90th percentile of the delay over all rows. Both measure how much leakage
the other offset would have caused, or, for the tables marked as leaking, does cause.

| Table                       | Time used                                                  | Entered later | Lag (90th pct.) | Status                                |
| --------------------------- | ---------------------------------------------------------- | ------------: | --------------: | ------------------------------------- |
| `admissionDx`               | `admitDxEnteredOffset` (entered)                           |             – |               – | OK                                    |
| `allergy`                   | `allergyEnteredOffset` (entered)                           |         99.1% |           8.4 h | OK                                    |
| `carePlanEOL`               | `cplEolDiscussionOffset` (discussion)                      |         97.8% |        6.6 days | **Leaks**; see below                  |
| `carePlanGeneral`           | `cplItemOffset` (entered)                                  |             – |               – | OK                                    |
| `carePlanGoal`              | `cplGoalOffset` (entered)                                  |             – |               – | OK, small revision risk               |
| `carePlanInfectiousDisease` | `cplInfectDiseaseOffset` (entered)                         |             – |               – | OK                                    |
| `diagnosis`                 | `diagnosisOffset` (entered)                                |             – |               – | OK                                    |
| `infusionDrug`              | `infusionOffset` (entered)                                 |             – |               – | OK                                    |
| `intakeOutput`              | `intakeOutputEntryOffset` (entered)                        |         42.6% |           2.5 h | OK                                    |
| `lab`                       | drawn: `labResultOffset`; result: `labResultRevisedOffset` |             – |               – | **5% of results leak**; see below     |
| `medication`                | `drugStartOffset`, `drugStopOffset`                        |         53.9% |            11 h | **Leaks**; see below                  |
| `nurseAssessment`           | `nurseAssessEntryOffset` (entered)                         |         91.0% |           3.3 h | OK                                    |
| `nurseCare`                 | `nurseCareEntryOffset` (entered)                           |         91.7% |           3.4 h | OK                                    |
| `nurseCharting`             | `nursingChartEntryOffset` (entered)                        |         25.7% |          57 min | OK                                    |
| `pastHistory`               | `pastHistoryEnteredOffset` (entered)                       |         98.1% |           7.1 h | OK                                    |
| `patient`                   | unit and hospital admission and discharge                  |             – |               – | OK, but see the anchoring issue above |
| `physicalExam`              | `physicalExamOffset` (entered; the only one)               |             – |               – | OK                                    |
| `respiratoryCharting`       | `respChartEntryOffset` (entered)                           |         24.4% |          47 min | OK                                    |
| `treatment`                 | `treatmentOffset` (entered)                                |             – |               – | OK                                    |
| `vitalAperiodic`            | `observationOffset` (entered; the only one)                |             – |               – | OK                                    |
| `vitalPeriodic`             | `observationOffset` (entered; the only one)                |             – |               – | OK, up to 5 min                       |

A dash means the table has one usable offset, so there is nothing to compare against.

### Tables with known leakage

**`carePlanEOL`.** `CAREPLAN_EOL` is timed at `cplEolDiscussionOffset`, when the end-of-life
discussion took place, but the row was saved (`cplEolSaveOffset`) later in 97.8% of the 1,433
rows: a median of 22 hours later, and 6.6 days or more for 10% of them. An end-of-life discussion
is a strong mortality signal, so a model can see it up to days before anyone documented it.
**Fix:** time it at `cplEolSaveOffset`, as the other care-plan tables already are.

**`medication`.** `MEDICATION//STARTED//<drug>` is timed at `drugStartOffset`, but in 53.9% of
orders the start is earlier than the order was entered (`drugOrderOffset`): a median of 31
minutes earlier, and 46 hours or more for 10% of those rows. Most hospitals sync orders into
eCareManager when the pharmacist verifies them, which likely explains why the recorded start so
often precedes the order. **Fix:** time
`STARTED` at the later of the two offsets. `MEDICATION//STOPPED//<drug>` has smaller problems: 3.0%
of stop offsets are exactly 0, which looks like a placeholder and puts the stop at unit admission,
and 7.1% are earlier than the start.

Cancelled orders (2.8% of rows) currently emit `STARTED` and `STOPPED` like any other order.
[#20](https://github.com/Medical-Event-Data-Standard/eICU_MEDS/pull/20) (for
[#10](https://github.com/Medical-Event-Data-Standard/eICU_MEDS/issues/10)) proposes emitting
`MEDICATION//ORDER_CANCELLED//<drug>` at the start offset instead. eICU records no cancellation time, so timing that
code at the start offset would reveal, at the planned start, a decision that may have been made
later. The latest of the row's offsets is the safer choice.

**`lab`.** The pipeline already splits a lab into `LAB//SPECIMEN_DRAWN//…`, which has no value, at
the draw time, and `LAB//RESULT//…`, which has the value, at `labResultRevisedOffset`, when the
final value was entered. That design is right, but 5.0% of results (1,974,810 rows) have a revised
offset earlier than the draw: a median of 34 minutes earlier, and 106 minutes or more for 10% of
them. Those results appear before their specimen was drawn. **Fix:** time the result at the later
of the two offsets.

### Smaller or unverifiable risks

- **`carePlanGoal` status, `carePlanInfectiousDisease` response.** The goal status (`Active`,
    `Resolved`, `Deferred`) and the response to therapy are part of the code at entry time. If a row
    were edited in place, the final status would leak back to the entry. The data suggests rows are
    a history instead: 62% of goals have several rows, and 78.6% of `Resolved` rows have an earlier
    `Active` row for the same goal. The rest cannot be checked. The response to therapy is filled in
    only 207 of 8,056 rows.
- **`vitalPeriodic`.** Values are 5-minute medians from the bedside monitor. The documentation does
    not say whether the offset marks the start or the end of the window, so a value can include up
    to 5 minutes of later signal.
- **`physicalExam`.** The vital-sign items include the lowest and highest values over 24 hours. They
    are assumed to cover the 24 hours before the exam; the documentation only says "24 hour range".
- **`patient` weights.** `ICU_ADMISSION_WEIGHT` and `ICU_ADMISSION_HEIGHT` are timed at unit admission.
    eICU has no entry time for them, so a weight measured hours later would still appear at
    admission.
- **Entered before the charted time.** In the charted tables, 0.7–3.6% of rows were entered before
    the time they are charted for (0.9% for `nurseCharting`, 0.7% for `respiratoryCharting`, 3.3% for
    `intakeOutput`, 3.6% for `nurseAssessment`, 3.5% for `nurseCare`). They are timed at entry, which
    is when they were in the record, so this is not leakage, but it points to pre-charting or clock
    errors.

## Tables left out because of leakage

| Table                 | Why                                                                                                                                                                                                                                    |
| --------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `microLab`            | Only the culture time is recorded. Organisms and sensitivities come back days later, with no result time.                                                                                                                              |
| `apachePatientResult` | Holds the actual ICU and hospital mortality, length of stay and ventilation days next to the APACHE predictions.                                                                                                                       |
| `apachePredVar`       | One row per stay with no offset. It includes `diedInHospital`, and the day-1 values summarize the first 24 hours, so any single timestamp would show them too early.                                                                   |
| `apacheApsVar`        | The worst values of the first APACHE day, one row per stay with no offset; same problem.                                                                                                                                               |
| `respiratoryCare`     | Not decided. A ventilation start first appears in the table a median 6.6 hours after it happened (90th percentile 15 hours), and ends are similar. `RESPIRATORY_CHARTING//…//RT Vent On/Off` already gives an entry-timed vent signal. |

The other excluded tables (`admissionDrug`, `carePlanCareProvider`, `customLab`, `note`) are left out
for reasons other than leakage; see
[Which eICU tables are extracted](../README.md#which-eicu-tables-are-extracted).

## Checklist for adding a table

1. Find every offset column in the
    [table documentation](https://github.com/MIT-LCP/eicu-code/tree/main/website/content/eicutables)
    and time each event at when it was entered. If an event's content comes from several offsets,
    use the latest of them.
2. Measure, on the full release and as aggregates only, how often the entered time differs from the
    event time and by how much, and record the numbers here.
3. Never emit columns that describe the end of the stay (`activeUponDischarge`, `actual*`,
    `diedInHospital`) or are cumulative to a later point.
4. Check that rows with a missing offset are dropped rather than becoming timeless.
5. Add the table to the table above, and any label-defining codes to the label table.

## For users of the output

- Use relative time within a subject only; never derive features from calendar dates.
- Cut every model input at the prediction time, and set the prediction time before the label period.
- Leave label-defining codes out of the inputs for the task at hand.
- Expect the same patient in several subjects; split by patient if you can recover patient identity.
- Fit vocabularies and normalization on the training split only.
