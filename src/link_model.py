"""Per-second X-band downlink model for GLOBAL-12 (NORAD 49772) passes over a ground station near Beijing."""

import math
import random
from datetime import datetime, timedelta, timezone
import numpy as np
from skyfield.api import EarthSatellite, wgs84

SAT_NAME, SAT_NORAD = "GLOBAL-12", 49772
GS_LAT_DEG, GS_LON_DEG, GS_ALT_M = 40.158936, 116.284731, 387.0
EL_MASK_DEG, COARSE_STEP_S, REFINE_MARGIN_S = 10.0, 10, 120
F_HZ, BN_HZ = 8.15e9, 0.2e6
PT_W, GT_DBI, GR_DBI, TX_LOSS_DB = 15.0, 6.0, 51.5, 0.0
T_RX_K, POL_LOSS_DB = 290.0, 0.4
THETA_3DB_DEG, PT_BASE_DEG, PT_WIND_K = 0.4, 0.05, 0.008
K_RAIN, ALPHA_RAIN, RAIN_H_KM, P_PCT = 0.010, 1.1, 5.0, 0.1
WET_DAY_MM, R_DRY_MMH, R_MAX_MMH = 0.1, 0.05, 80.0
# month: (precipitation mm, rain days, reference rain rate R0.01 mm/h)
MONTHLY_RAIN = {
    1: (0.0, 0, 0.5),
    2: (0.0, 0, 0.5),
    3: (1.6, 1, 2.0),
    4: (21.1, 2, 8.0),
    5: (57.5, 6, 18.0),
    6: (90.8, 5, 32.0),
    7: (328.6, 12, 52.0),
    8: (292.6, 14, 48.0),
    9: (58.2, 4, 22.0),
    10: (119.1, 6, 28.0),
    11: (5.5, 1, 4.0),
    12: (3.9, 1, 2.0),
}
GAP_DB, PROTO_EFF, S_PKT_BYTES = 1.5, 0.90, 1522
PKT_BITS = 8 * S_PKT_BYTES
LOSS_BASE, LOSS_BURST, P_BURST, BURST_RAIN_GAIN, BURST_RAIN_REF_DB = (
    0.01,
    0.20,
    0.03,
    1.5,
    5.0,
)
K_B = 1.380649e-23
LOCAL_TZ = timezone(timedelta(hours=8))
PARAMETERS = (
    "SAT_NAME",
    "SAT_NORAD",
    "GS_LAT_DEG",
    "GS_LON_DEG",
    "GS_ALT_M",
    "EL_MASK_DEG",
    "COARSE_STEP_S",
    "REFINE_MARGIN_S",
    "F_HZ",
    "BN_HZ",
    "PT_W",
    "GT_DBI",
    "GR_DBI",
    "TX_LOSS_DB",
    "T_RX_K",
    "POL_LOSS_DB",
    "THETA_3DB_DEG",
    "PT_BASE_DEG",
    "PT_WIND_K",
    "K_RAIN",
    "ALPHA_RAIN",
    "RAIN_H_KM",
    "P_PCT",
    "WET_DAY_MM",
    "R_DRY_MMH",
    "R_MAX_MMH",
    "MONTHLY_RAIN",
    "GAP_DB",
    "PROTO_EFF",
    "S_PKT_BYTES",
    "LOSS_BASE",
    "LOSS_BURST",
    "P_BURST",
    "BURST_RAIN_GAIN",
    "BURST_RAIN_REF_DB",
    "K_B",
)
WEATHER_FIELDS = {
    "temp_c": 2,
    "pressure_hpa": 1,
    "rh_pct": 0,
    "water_vapour_gm3": 4,
    "precip_mm": 1,
    "rain_rate_mmh": 2,
    "wind_kmh": 1,
    "pointing_error_deg": 4,
}
ISO_UTC = "%Y-%m-%dT%H:%M:%SZ"


def parameters():
    return {name: globals()[name] for name in PARAMETERS}


def mib(bits):
    return bits / (8 * 1024 * 1024)


def gas_zenith_db(f_hz, p_hpa, rho_gm3):
    f = f_hz / 1e9
    return 0.00025 * (p_hpa / 1013.0) * (f**2) + 0.0030 * (rho_gm3 / 7.5) * f


def gas_slant_db(f_hz, el_deg, p_hpa, rho_gm3):
    return gas_zenith_db(f_hz, p_hpa, rho_gm3) / math.sin(
        math.radians(max(el_deg, 0.1))
    )


def sky_noise_k(gas_db, t_k):
    return t_k * (1.0 - math.exp(-gas_db / 4.343))


def rain_db(el_deg, rain_mmh, p_pct):
    if not math.isfinite(p_pct) or not 0 < p_pct <= 100:
        raise ValueError("exceedance probability must lie in (0, 100] %")
    path_km = max(0.0, (RAIN_H_KM - GS_ALT_M / 1000.0)) / math.sin(
        math.radians(max(el_deg, 0.1))
    )
    return (
        K_RAIN * (rain_mmh**ALPHA_RAIN) * (0.7 * path_km) * ((p_pct / 0.01) ** (-0.12))
    )


def fspl_db(f_hz, r_m):
    return (
        32.44
        + 20.0 * math.log10(f_hz / 1e6)
        + 20.0 * math.log10(max(r_m, 1.0) / 1000.0)
    )


def pointing_loss_db(err_deg, beamwidth_deg):
    return 10.0 * math.log10(math.e) * 2.77 * (err_deg / beamwidth_deg) ** 2


def pointing_error_deg(wind_kmh):
    return math.sqrt(PT_BASE_DEG**2 + (PT_WIND_K * math.sqrt(max(wind_kmh, 0.0))) ** 2)


def water_vapour_gm3(rh_pct, t_c):
    e_hpa = 0.61078 * math.exp(17.27 * t_c / (t_c + 237.3)) * rh_pct / 100.0 * 10.0
    return max(0.01, 217.0 * e_hpa / (t_c + 273.15))


def rain_rate_mmh(month, precip_mm):
    total_mm, rain_days, r001 = MONTHLY_RAIN[month]
    if precip_mm <= WET_DAY_MM:
        return R_DRY_MMH
    return min(r001 * (precip_mm / max(total_mm / max(rain_days, 1), 0.1)), R_MAX_MMH)


def link_weather(rec):
    values = (
        rec["temp_mean_C"],
        rec["press_hPa"],
        rec["rh_pct"],
        rec["prec_mm"],
        rec["wind_max_kmh"],
    )
    if not all(
        isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)
        for v in values
    ):
        raise ValueError(f"weather {rec.get('date')}: missing or non-numeric value")
    t_c, p_hpa, rh, precip, wind = values
    if wind < 0 or precip < 0 or p_hpa <= 0 or not 0 <= rh <= 100 or t_c <= -273.15:
        raise ValueError(f"weather {rec.get('date')}: value outside its physical range")
    return {
        "temp_c": t_c,
        "temp_k": t_c + 273.15,
        "pressure_hpa": p_hpa,
        "rh_pct": rh,
        "water_vapour_gm3": water_vapour_gm3(rh, t_c),
        "precip_mm": precip,
        "rain_rate_mmh": rain_rate_mmh(int(rec["date"][5:7]), precip),
        "wind_kmh": wind,
        "pointing_error_deg": pointing_error_deg(wind),
    }


def compute_day(line1, line2, ds, weather, ts, seed):
    rng = random.Random(seed + int(ds))
    link = EarthSatellite(line1, line2, SAT_NAME, ts) - wgs84.latlon(
        GS_LAT_DEG, GS_LON_DEG, elevation_m=GS_ALT_M
    )
    t0 = datetime.strptime(ds, "%Y%m%d").replace(tzinfo=timezone.utc)
    t1 = t0 + timedelta(days=1)
    cache = {}

    def weather_at(t):
        key = t.astimezone(LOCAL_TZ).strftime("%Y%m%d")
        if key not in weather:
            raise ValueError(
                f"no weather for local date {key} required at {t.isoformat()}"
            )
        if key not in cache:
            cache[key] = link_weather(weather[key])
        return key, cache[key]

    wx0 = weather_at(t0)[1]
    eirp = 10 * math.log10(PT_W) + GT_DBI - TX_LOSS_DB
    noise_db = 10 * math.log10(K_B * BN_HZ)
    coarse = [
        t0 + timedelta(seconds=i * COARSE_STEP_S) for i in range(86400 // COARSE_STEP_S)
    ]
    visible = link.at(ts.utc(coarse)).altaz()[0].degrees > EL_MASK_DEG
    edges = np.flatnonzero(np.diff(np.r_[0, visible.astype(int), 0]))
    day = dict.fromkeys(
        ("bits", "effective_bits", "snr", "rain", "rain_free", "pointing", "fspl"), 0.0
    )
    w_sum = dict.fromkeys(WEATHER_FIELDS, 0.0)
    passes, contact_s, peak_rate = [], 0, 0.0
    for i0, i1 in zip(edges[::2].tolist(), (edges[1::2] - 1).tolist()):
        start = max(
            t0,
            t0
            + timedelta(seconds=i0 * COARSE_STEP_S)
            - timedelta(seconds=REFINE_MARGIN_S),
        )
        end = min(
            t1,
            t0
            + timedelta(seconds=i1 * COARSE_STEP_S)
            + timedelta(seconds=REFINE_MARGIN_S),
        )
        # The UTC day is half-open: t1 belongs to the next day.
        fine = [
            start + timedelta(seconds=i)
            for i in range(int((end - start).total_seconds()) + (end < t1))
        ]
        alt, _, dist = link.at(ts.utc(fine)).altaz()
        idx = np.flatnonzero(alt.degrees > EL_MASK_DEG)
        if not idx.size:
            continue
        els, rngs = alt.degrees[idx[0] : idx[-1] + 1], dist.m[idx[0] : idx[-1] + 1]
        first = start + timedelta(seconds=int(idx[0]))
        s = dict.fromkeys(
            ("bits", "effective_bits", "snr", "rain", "pointing", "fspl"), 0.0
        )
        snr_peak, rain_at_peak, t_peak, rain_max, pointing_peak, dates = (
            -math.inf,
            0.0,
            None,
            0.0,
            0.0,
            [],
        )
        for i, (el, r) in enumerate(zip(els, rngs)):
            t = first + timedelta(seconds=i)
            key, wx = weather_at(t)
            if not dates or dates[-1] != key:
                dates.append(key)
            ls = fspl_db(F_HZ, r)
            la = gas_slant_db(F_HZ, el, wx["pressure_hpa"], wx["water_vapour_gm3"])
            fr = rain_db(el, wx["rain_rate_mmh"], P_PCT)
            lp = pointing_loss_db(wx["pointing_error_deg"], THETA_3DB_DEG)
            gt = GR_DBI - 10 * math.log10(
                max(sky_noise_k(la, wx["temp_k"]) + T_RX_K, 1e-6)
            )
            snr = eirp + gt - (ls + la + fr + POL_LOSS_DB + lp) - noise_db
            rate = max(
                0.0,
                BN_HZ * math.log2(1.0 + 10.0 ** ((snr - GAP_DB) / 10.0)) * PROTO_EFF,
            )
            bits = math.floor(rate / PKT_BITS) * PKT_BITS
            p_burst = P_BURST * (1.0 + BURST_RAIN_GAIN * (fr / BURST_RAIN_REF_DB))
            loss = LOSS_BURST if rng.random() < p_burst else LOSS_BASE
            for k, v in (
                ("bits", bits),
                ("effective_bits", bits * (1 - loss)),
                ("snr", snr),
                ("rain", fr),
                ("pointing", lp),
                ("fspl", ls),
            ):
                s[k] += v
            if snr > snr_peak:
                snr_peak, rain_at_peak, t_peak = snr, fr, t
            rain_max, pointing_peak, peak_rate = (
                max(rain_max, fr),
                max(pointing_peak, lp),
                max(peak_rate, rate / 1e6),
            )
            for f in WEATHER_FIELDS:
                w_sum[f] += wx[f]
        n = len(els)
        contact_s += n
        for k in s:
            day[k] += s[k]
        day["rain_free"] += s["snr"] + s["rain"]
        passes.append(
            {
                "start_utc": first.strftime(ISO_UTC),
                "duration_s": n,
                "max_elevation_deg": round(float(np.max(els)), 2),
                "min_range_km": round(float(np.min(rngs) / 1000.0), 2),
                "bits": s["bits"],
                "effective_bits": s["effective_bits"],
                "snr_mean_db": s["snr"] / n,
                "rain_free_snr_mean_db": (s["snr"] + s["rain"]) / n,
                "rain_mean_db": s["rain"] / n,
                "fspl_mean_db": s["fspl"] / n,
                "pointing_loss_mean_db": s["pointing"] / n,
                "snr_peak_db": snr_peak,
                "snr_peak_utc": t_peak.strftime(ISO_UTC),
                "rain_at_snr_peak_db": rain_at_peak,
                "rain_free_snr_at_peak_db": snr_peak + rain_at_peak,
                "rain_max_db": rain_max,
                "pointing_loss_peak_db": pointing_peak,
                "weather_dates": [f"{d[:4]}-{d[4:6]}-{d[6:]}" for d in dates],
            }
        )
    w = {f: w_sum[f] / contact_s for f in WEATHER_FIELDS} if contact_s else wx0
    total, eff = mib(day["bits"]), mib(day["effective_bits"])

    def mean(key, digits):
        return round(day[key] / contact_s, digits) if contact_s else None

    return {
        "n_passes": len(passes),
        "contact_s": contact_s,
        "total_mib": round(total, 2),
        "effective_mib": round(eff, 2),
        "lost_mib": round(total - eff, 2),
        "peak_rate_mbps": round(peak_rate, 3),
        "snr_mean_db": mean("snr", 6),
        "rain_free_snr_mean_db": mean("rain_free", 6),
        "rain_mean_db": mean("rain", 6),
        "fspl_mean_db": mean("fspl", 6),
        "pointing_loss_mean_db": mean("pointing", 4),
        **{f: round(w[f], digits) for f, digits in WEATHER_FIELDS.items()},
        "snr_sum_db_s": day["snr"],
        "rain_free_snr_sum_db_s": day["rain_free"],
        "rain_sum_db_s": day["rain"],
        "fspl_sum_db_s": day["fspl"],
        "pointing_loss_sum_db_s": day["pointing"],
        "weather_sum": w_sum,
        "passes": passes,
    }
