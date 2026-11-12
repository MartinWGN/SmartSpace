"""Simulate every UTC day of 2025 and write per-day records, a daily table and period statistics to results/."""

import argparse
import csv
import hashlib
import json
import platform
from datetime import date, datetime, timedelta
from importlib.metadata import version
from pathlib import Path

from skyfield.api import load

import link_model as lm

ROOT = Path(__file__).resolve().parents[1]
ARCHIVE = ROOT / "input" / "GLOBAL-12_49772_2025.json"
WEATHER = ROOT / "input" / "beijing_daily_full_weather_2025.json"
SOURCES = (Path(lm.__file__).resolve(), Path(__file__).resolve())
DATES = [(date(2025, 1, 1) + timedelta(days=i)).strftime("%Y%m%d") for i in range(365)]
# Sample sums stay in memory for the weighted means. Daily files keep the means and the passes.
INTERNAL = (
    "snr_sum_db_s",
    "rain_free_snr_sum_db_s",
    "rain_sum_db_s",
    "fspl_sum_db_s",
    "pointing_loss_sum_db_s",
    "weather_sum",
)
SEASONS = {
    "winter": [1, 2, 12],
    "spring": [3, 4, 5],
    "summer": [6, 7, 8],
    "autumn": [9, 10, 11],
}
MEANS = {
    "snr_mean_db": "snr_sum_db_s",
    "rain_free_snr_mean_db": "rain_free_snr_sum_db_s",
    "rain_mean_db": "rain_sum_db_s",
    "fspl_mean_db": "fspl_sum_db_s",
    "pointing_loss_mean_db": "pointing_loss_sum_db_s",
}
CSV_FIELDS = (
    "date",
    "month",
    "status",
    "n_passes",
    "contact_s",
    "total_mib",
    "effective_mib",
    "lost_mib",
    "peak_rate_mbps",
    *MEANS,
    *lm.WEATHER_FIELDS,
)
DEFINITIONS = {
    "data_unit": "MiB = 2^20 bytes",
    "link_means": "Arithmetic means in dB over all one-second samples above the elevation mask; monthly, seasonal "
    "and annual values weight every sample equally.",
    "rain_free_snr": "Effective SNR plus the rain attenuation of the same sample; gas absorption, sky noise and "
    "pointing loss are retained.",
    "weather_means": "Contact-time-weighted means of the daily weather values applied to the samples.",
    "coverage": "UTC days without a TLE epoch are unassessed: they are excluded from all statistics and are not "
    "counted as zero capacity.",
    "mean_daily_effective_mib": "Total effective data divided by the number of evaluated days.",
    "seasons": "Calendar months of 2025; winter combines January, February and December.",
    "random_seed": "Packet-loss draws use random.Random(seed + int(YYYYMMDD)) for each UTC day.",
}


def load_tle_archive(path):
    tle = {}
    for rec in sorted(
        json.loads(Path(path).read_text(encoding="utf-8")),
        key=lambda r: r.get("EPOCH", ""),
    ):
        line1, line2 = rec.get("TLE_LINE1", ""), rec.get("TLE_LINE2", "")
        try:
            ds = datetime.fromisoformat(rec["EPOCH"].replace("Z", "+00:00")).strftime(
                "%Y%m%d"
            )
        except (KeyError, ValueError):
            continue
        if line1.startswith("1 ") and line2.startswith("2 "):
            tle.setdefault(ds, (line1, line2, rec["EPOCH"]))
    return tle


def build_provenance(archive, weather, seed):
    def sha(path):
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()

    prov = {
        "source_sha256": {p.name: sha(p) for p in SOURCES},
        "tle_sha256": sha(archive),
        "weather_sha256": sha(weather),
        "parameters": lm.parameters(),
        "random_seed": seed,
    }
    payload = json.dumps(prov, sort_keys=True, separators=(",", ":"), allow_nan=False)
    runtime = {
        "python": platform.python_version(),
        **{m: version(m) for m in ("numpy", "skyfield", "sgp4")},
    }
    return hashlib.sha256(payload.encode("utf-8")).hexdigest(), {
        **prov,
        "runtime": runtime,
    }


def simulate_day(ds, tle, weather, ts, seed):
    head = {"date": f"{ds[:4]}-{ds[4:6]}-{ds[6:]}", "month": int(ds[4:6])}
    next_ds = (datetime.strptime(ds, "%Y%m%d") + timedelta(days=1)).strftime("%Y%m%d")
    if ds not in tle:
        return {**head, "status": "unassessed_no_tle"}
    if ds not in weather or next_ds not in weather:
        return {**head, "status": "unassessed_no_weather"}
    line1, line2, epoch = tle[ds]
    return {
        **head,
        "status": "evaluated",
        "tle_epoch": epoch,
        **lm.compute_day(line1, line2, ds, weather, ts, seed),
    }


def summarize(records):
    ev = [r for r in records if r["status"] == "evaluated"]
    n = sum(r["contact_s"] for r in ev)
    total, eff = sum(r["total_mib"] for r in ev), sum(r["effective_mib"] for r in ev)
    out = {
        "n_days": len(records),
        "n_evaluated_days": len(ev),
        "n_unassessed_days": len(records) - len(ev),
        "n_no_pass_days": sum(not r["n_passes"] for r in ev),
        "n_passes": sum(r["n_passes"] for r in ev),
        "contact_s": n,
        "total_mib": round(total, 2),
        "effective_mib": round(eff, 2),
        "efficiency": round(eff / total, 4) if total else None,
        "mean_daily_effective_mib": round(eff / len(ev), 6) if ev else None,
    }
    out.update(
        {
            k: round(sum(r[s] for r in ev) / n, 6) if n else None
            for k, s in MEANS.items()
        }
    )
    out.update(
        {
            k: round(sum(r["weather_sum"][k] for r in ev) / n, 2) if n else None
            for k in ("temp_c", "pressure_hpa", "rh_pct")
        }
    )
    return out


def for_json(record):
    return {k: v for k, v in record.items() if k not in INTERNAL}


def write_json(path, data):
    Path(path).write_text(
        json.dumps(data, indent=2, ensure_ascii=False, allow_nan=False) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def run(out_dir, archive=ARCHIVE, weather_path=WEATHER, seed=2025):
    out_dir = Path(out_dir)
    tle, weather = load_tle_archive(archive), json.loads(
        Path(weather_path).read_text(encoding="utf-8")
    )
    fingerprint, provenance = build_provenance(archive, weather_path, seed)
    ts, records = load.timescale(), []
    for i, ds in enumerate(DATES, 1):
        records.append(simulate_day(ds, tle, weather, ts, seed))
        print(f"\rSimulated {i}/{len(DATES)} days", end="", flush=True)
    print()
    (out_dir / "daily").mkdir(parents=True, exist_ok=True)
    for ds, r in zip(DATES, records):
        write_json(out_dir / "daily" / f"{ds}.json", for_json(r))
    with open(out_dir / "daily.csv", "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f, lineterminator="\n")
        writer.writerow(CSV_FIELDS)
        writer.writerows([r.get(k) for k in CSV_FIELDS] for r in records)
    ev = [r for r in records if r["status"] == "evaluated"]

    def extreme(pick):
        if not ev:
            return None
        record = pick(ev, key=lambda r: r["effective_mib"])
        return {key: record[key] for key in ("date", "effective_mib")}

    summary = {
        "satellite": {"name": lm.SAT_NAME, "norad_id": lm.SAT_NORAD},
        "ground_station": {
            "lat_deg": lm.GS_LAT_DEG,
            "lon_deg": lm.GS_LON_DEG,
            "alt_m": lm.GS_ALT_M,
        },
        "period_utc": [records[0]["date"], records[-1]["date"]],
        "definitions": DEFINITIONS,
        "run_fingerprint": fingerprint,
        "provenance": provenance,
        "annual": {
            **summarize(records),
            "best_day": extreme(max),
            "worst_day": extreme(min),
        },
        "monthly": {
            str(m): summarize([r for r in records if r["month"] == m])
            for m in range(1, 13)
        },
        "seasonal": {
            k: {"months": v, **summarize([r for r in records if r["month"] in v])}
            for k, v in SEASONS.items()
        },
    }
    write_json(out_dir / "summary.json", summary)
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=ROOT / "results")
    parser.add_argument("--archive", type=Path, default=ARCHIVE)
    parser.add_argument("--weather", type=Path, default=WEATHER)
    parser.add_argument("--seed", type=int, default=2025)
    parser.add_argument(
        "--date",
        help="simulate one UTC day (YYYYMMDD) and print its record without writing files",
    )
    args = parser.parse_args()
    if args.date:
        if args.date not in DATES:
            parser.error("--date must be a day of 2025 written as YYYYMMDD")
        weather = json.loads(args.weather.read_text(encoding="utf-8"))
        record = simulate_day(
            args.date,
            load_tle_archive(args.archive),
            weather,
            load.timescale(),
            args.seed,
        )
        print(json.dumps(record, indent=2))
        return
    s = run(args.output_dir, args.archive, args.weather, args.seed)["annual"]
    print(
        f"Evaluated days: {s['n_evaluated_days']}/{s['n_days']} ({s['n_unassessed_days']} unassessed)"
    )
    print(f"Passes: {s['n_passes']}; contact time: {s['contact_s']} s")
    if not s["n_evaluated_days"]:
        print("No days were evaluated; check the TLE and weather coverage.")
        return
    print(
        f"Effective data: {s['effective_mib']:.2f} MiB; mean {s['mean_daily_effective_mib']:.2f} MiB/day"
    )
    if not s["contact_s"]:
        print("No contact samples were above the elevation mask.")
        return
    print(
        f"Mean SNR {s['snr_mean_db']:.3f} dB; rain attenuation {s['rain_mean_db']:.3f} dB; "
        f"FSPL {s['fspl_mean_db']:.3f} dB"
    )
    print(
        f"Best day {s['best_day']['date']} ({s['best_day']['effective_mib']:.2f} MiB); "
        f"worst day {s['worst_day']['date']} ({s['worst_day']['effective_mib']:.2f} MiB)"
    )


if __name__ == "__main__":
    main()
