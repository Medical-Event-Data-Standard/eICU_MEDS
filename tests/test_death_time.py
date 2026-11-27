"""When `MEDS_DEATH` is timed (#23): the `patient` table's join and `time_of_death`, on a few rows."""

from datetime import datetime
from pathlib import Path

import polars as pl
from MEDS_extract.config import MessyConfig

MESSY_CFG = Path(__file__).parents[1] / "src" / "eICU_MEDS" / "configs" / "event_configs.yaml"

# Three hospital stays, all ending Expired at 2015-12-31 12:00 (offsets are from each unit admission):
#   7: unit 1 Alive, then unit 2 Expired 60 min before the hospital discharge  -> unit 2's discharge
#   8: no unit Expired                                                         -> hospital discharge
#   9: two units Expired (contradictory)                                       -> hospital discharge
ROWS = {
    "patientunitstayid": [1, 2, 3, 4, 5],
    "patienthealthsystemstayid": [7, 7, 8, 9, 9],
    "hospitalid": [1, 1, 1, 1, 1],
    "hospitaldischargeyear": [2015] * 5,
    "hospitaldischargetime24": ["12:00:00"] * 5,
    "hospitaldischargeoffset": [3000, 600, 600, 600, 300],
    "unitdischargeoffset": [1000, 540, 100, 500, 200],
    "hospitaladmitoffset": [0, -2400, 0, 0, -300],
    "age": ["60"] * 5,
    "hospitaldischargestatus": ["Expired"] * 5,
    "unitdischargestatus": ["Alive", "Expired", "Alive", "Expired", "Expired"],
}
HOSPITAL_DISCHARGE = datetime(2015, 12, 31, 12)


def _time_of_death(tmp_path: Path) -> dict[int, set]:
    patient = next(t for t in MessyConfig.load(MESSY_CFG).event_tables if t.input_prefix == "patient")
    pl.DataFrame({"hospitalid": [1], "numbedscategory": ["x"], "teachingstatus": ["x"], "region": ["x"]}).write_csv(
        tmp_path / "hospital.csv"
    )
    pl.DataFrame(ROWS).select(
        "patienthealthsystemstayid",
        pl.col("unitdischargestatus").alias("unit_discharge_status"),
        pl.col("patientunitstayid").alias("icu_deaths"),
    ).write_csv(tmp_path / "unit_discharge_status.csv")
    left = pl.LazyFrame(ROWS)
    out = patient.prepare(patient.apply_join(left, tmp_path)).collect()
    return {
        stay: {t for t in times if t is not None}
        for stay, times in out.group_by("patienthealthsystemstayid").agg("time_of_death").iter_rows()
    }


def test_death_is_timed_at_the_one_expired_icu_discharge(tmp_path):
    deaths = _time_of_death(tmp_path)
    assert deaths[7] == {datetime(2015, 12, 31, 11)}  # unit 2: discharge 600 - 540 = 60 min early
    assert deaths[8] == {HOSPITAL_DISCHARGE}
    assert deaths[9] == {HOSPITAL_DISCHARGE}
