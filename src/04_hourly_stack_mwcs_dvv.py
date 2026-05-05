#!/usr/bin/env python3
"""Hourly stack, MWCS, and dv/v workflow built on MSNoise outputs.

This script starts from MSNoise ``keep_all=Y`` CCF files in
``CROSS_CORRELATIONS``. It keeps the core numerical methods aligned with
MSNoise by using:

- ``msnoise.api.stack`` for stacking 30-minute CCF rows into hourly CCF rows
- ``msnoise.move2obspy.mwcs`` for moving-window cross-spectral delay estimates
- ``obspy.signal.regression.linear_regression`` for the DTT weighted regressions

It intentionally writes independent hourly products instead of patching
MSNoise's day-based ``STACKS/MWCS/DTT`` workflow.
"""

from __future__ import annotations

import argparse
import glob
import os
import sqlite3
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, Iterator, List, Optional, Sequence, Tuple

import numpy as np
import pandas as pd
from obspy.geodetics import gps2dist_azimuth
from obspy.signal.regression import linear_regression

from msnoise.api import stack as msnoise_stack
from msnoise.move2obspy import mwcs as msnoise_mwcs

from config_loader import load_config


COMPONENT = "ZZ"
DB_PATH = "msnoise.sqlite"
CC_ROOT = Path("CROSS_CORRELATIONS")
STACK_ROOT = Path("HOURLY_STACKS")
MWCS_ROOT = Path("HOURLY_MWCS")
DVV_ROOT = Path("HOURLY_DVV")
TXT_ROOT = Path("HOURLY_TXT")


def optional_value(value):
    return None if value in (None, "") else value


def optional_int(value):
    return None if value in (None, "") else int(value)


def bool_value(value, default: bool) -> bool:
    if value in (None, ""):
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "t", "yes", "y", "on"}


def hourly_defaults(config_path: str) -> dict[str, object]:
    config = load_config(config_path)
    scan = config.get("data_scan", {})
    hourly = config.get("hourly_processing", {})
    visualization = config.get("visualization", {})
    component = hourly.get("component", visualization.get("component", COMPONENT))
    return {
        "db": hourly.get("db_path", scan.get("db_path", DB_PATH)),
        "cc_root": hourly.get("cc_root", "CROSS_CORRELATIONS"),
        "stack_root": hourly.get("stack_root", STACK_ROOT),
        "mwcs_root": hourly.get("mwcs_root", MWCS_ROOT),
        "dvv_root": hourly.get("dvv_root", DVV_ROOT),
        "txt_root": hourly.get("txt_root", "HOURLY_TXT"),
        "component": component,
        "filters": optional_value(hourly.get("filters")),
        "min_windows_per_hour": hourly.get("min_windows_per_hour", 1),
        "reference_start": optional_value(hourly.get("reference_start")),
        "reference_end": optional_value(hourly.get("reference_end")),
        "force": bool_value(hourly.get("force"), False),
        "include_all": bool_value(hourly.get("include_all"), True),
        "export_txt": bool_value(hourly.get("export_txt"), True),
        "min_dtt_points": hourly.get("min_dtt_points", 3),
        "max_pairs": optional_int(hourly.get("max_pairs")),
        "max_days": optional_int(hourly.get("max_days")),
    }


@dataclass(frozen=True)
class FilterConfig:
    ref: int
    low: float
    high: float
    mwcs_low: float
    mwcs_high: float
    mwcs_wlen: float
    mwcs_step: float


@dataclass(frozen=True)
class RuntimeConfig:
    cc_sampling_rate: float
    maxlag: float
    stack_method: str
    pws_timegate: float
    pws_power: float
    dtt_lag: str
    dtt_v: float
    dtt_minlag: float
    dtt_width: float
    dtt_sides: str
    dtt_mincoh: float
    dtt_maxerr: float
    dtt_maxdt: float


def parse_args() -> argparse.Namespace:
    config_parser = argparse.ArgumentParser(add_help=False)
    config_parser.add_argument("--config", default="config.json")
    config_args, _ = config_parser.parse_known_args()
    defaults = hourly_defaults(config_args.config)

    parser = argparse.ArgumentParser(
        description="Compute hourly stacks, MWCS, and dv/v from MSNoise keep_all CCF HDF5 files."
    )
    parser.add_argument("--config", default=config_args.config, help="Workflow JSON config file.")
    parser.add_argument(
        "--stage",
        choices=("all", "stack", "mwcs", "dtt", "export"),
        default="all",
        help="Pipeline stage to run.",
    )
    parser.add_argument("--db", default=str(defaults["db"]), help="MSNoise SQLite database path.")
    parser.add_argument("--cc-root", default=str(defaults["cc_root"]), help="Input CROSS_CORRELATIONS root.")
    parser.add_argument("--stack-root", default=str(defaults["stack_root"]), help="Output hourly stack root.")
    parser.add_argument("--mwcs-root", default=str(defaults["mwcs_root"]), help="Output hourly MWCS root.")
    parser.add_argument("--dvv-root", default=str(defaults["dvv_root"]), help="Output hourly dv/v root.")
    parser.add_argument("--txt-root", default=str(defaults["txt_root"]), help="Output hourly txt root.")
    parser.add_argument("--component", default=str(defaults["component"]), help="Component to process, e.g. ZZ.")
    parser.add_argument(
        "--filters",
        default=defaults["filters"],
        help="Comma-separated filter ids to process. Default: all used filters in DB.",
    )
    parser.add_argument(
        "--min-windows-per-hour",
        type=int,
        default=int(defaults["min_windows_per_hour"]),
        help="Minimum 30-min CCF rows required to create an hourly stack.",
    )
    parser.add_argument(
        "--reference-start",
        default=defaults["reference_start"],
        help="Optional inclusive reference start timestamp/date for REF stack.",
    )
    parser.add_argument(
        "--reference-end",
        default=defaults["reference_end"],
        help="Optional inclusive reference end timestamp/date for REF stack.",
    )
    parser.add_argument(
        "--force",
        action=argparse.BooleanOptionalAction,
        default=defaults["force"],
        help="Overwrite existing outputs.",
    )
    parser.add_argument(
        "--include-all",
        action=argparse.BooleanOptionalAction,
        default=defaults["include_all"],
        help="Include network ALL dv/v rows.",
    )
    parser.add_argument(
        "--export-txt",
        action=argparse.BooleanOptionalAction,
        default=defaults["export_txt"],
        help="Export hourly txt files after DTT.",
    )
    parser.add_argument(
        "--min-dtt-points",
        type=int,
        default=int(defaults["min_dtt_points"]),
        help="Minimum selected MWCS lag windows required for each dv/v regression.",
    )
    parser.add_argument("--max-pairs", type=int, default=defaults["max_pairs"], help="Debug limit on pair count per filter.")
    parser.add_argument("--max-days", type=int, default=defaults["max_days"], help="Debug limit on daily files per pair.")
    return parser.parse_args()


def connect(db_path: str) -> sqlite3.Connection:
    if not os.path.exists(db_path):
        raise FileNotFoundError(f"MSNoise database not found: {db_path}")
    return sqlite3.connect(db_path)


def get_config_value(conn: sqlite3.Connection, name: str, default: str) -> str:
    row = conn.execute("SELECT value FROM config WHERE name = ?", (name,)).fetchone()
    return str(row[0]) if row else default


def load_runtime_config(conn: sqlite3.Connection) -> RuntimeConfig:
    return RuntimeConfig(
        cc_sampling_rate=float(get_config_value(conn, "cc_sampling_rate", "20.0")),
        maxlag=float(get_config_value(conn, "maxlag", "120.0")),
        stack_method=get_config_value(conn, "stack_method", "linear"),
        pws_timegate=float(get_config_value(conn, "pws_timegate", "10.0")),
        pws_power=float(get_config_value(conn, "pws_power", "2.0")),
        dtt_lag=get_config_value(conn, "dtt_lag", "static"),
        dtt_v=float(get_config_value(conn, "dtt_v", "1.0")),
        dtt_minlag=float(get_config_value(conn, "dtt_minlag", "5.0")),
        dtt_width=float(get_config_value(conn, "dtt_width", "30.0")),
        dtt_sides=get_config_value(conn, "dtt_sides", "both"),
        dtt_mincoh=float(get_config_value(conn, "dtt_mincoh", "0.65")),
        dtt_maxerr=float(get_config_value(conn, "dtt_maxerr", "0.1")),
        dtt_maxdt=float(get_config_value(conn, "dtt_maxdt", "0.1")),
    )


def load_filters(conn: sqlite3.Connection, requested: Optional[str]) -> List[FilterConfig]:
    rows = conn.execute(
        """
        SELECT ref, low, high, mwcs_low, mwcs_high, mwcs_wlen, mwcs_step
        FROM filters
        WHERE used = 1
        ORDER BY ref
        """
    ).fetchall()
    filters = [
        FilterConfig(
            ref=int(row[0]),
            low=float(row[1]),
            high=float(row[2]),
            mwcs_low=float(row[3]),
            mwcs_high=float(row[4]),
            mwcs_wlen=float(row[5]),
            mwcs_step=float(row[6]),
        )
        for row in rows
    ]
    if requested:
        wanted = {int(item.strip()) for item in requested.split(",") if item.strip()}
        filters = [f for f in filters if f.ref in wanted]
    if not filters:
        raise ValueError("No filters selected.")
    return filters


def load_station_coordinates(conn: sqlite3.Connection) -> Dict[str, Tuple[float, float]]:
    rows = conn.execute("SELECT net, sta, X, Y FROM stations WHERE used = 1").fetchall()
    coords: Dict[str, Tuple[float, float]] = {}
    for net, sta, lon, lat in rows:
        coords[f"{net}.{sta}"] = (float(lon), float(lat))
    return coords


def station_pair_name(sta1: str, sta2: str) -> str:
    return f"{sta1.replace('.', '_')}_{sta2.replace('.', '_')}"


def split_pair_name(pair: str) -> Tuple[str, str]:
    parts = pair.split("_")
    if len(parts) != 4:
        raise ValueError(f"Cannot parse pair name: {pair}")
    return f"{parts[0]}.{parts[1]}", f"{parts[2]}.{parts[3]}"


def interstation_distance_km(
    sta1: str, sta2: str, station_coords: Dict[str, Tuple[float, float]]
) -> float:
    if sta1 == sta2:
        return 0.0
    if sta1 not in station_coords or sta2 not in station_coords:
        return 0.0
    lon1, lat1 = station_coords[sta1]
    lon2, lat2 = station_coords[sta2]
    dist_m, _, _ = gps2dist_azimuth(lat1, lon1, lat2, lon2)
    return dist_m / 1000.0


def discover_cc_groups(
    cc_root: Path,
    filters: Sequence[FilterConfig],
    component: str,
    allowed_stations: Optional[set[str]],
    max_pairs: Optional[int],
    max_days: Optional[int],
) -> Dict[int, Dict[Tuple[str, str], List[Path]]]:
    groups: Dict[int, Dict[Tuple[str, str], List[Path]]] = {}
    for fcfg in filters:
        pattern = cc_root / f"{fcfg.ref:02d}" / "*" / "*" / component / "*.h5"
        pair_files: Dict[Tuple[str, str], List[Path]] = defaultdict(list)
        for filename in sorted(glob.glob(str(pattern))):
            path = Path(filename)
            sta2 = path.parent.parent.name
            sta1 = path.parent.parent.parent.name
            if allowed_stations and (sta1 not in allowed_stations or sta2 not in allowed_stations):
                continue
            pair_files[(sta1, sta2)].append(path)
        if max_pairs is not None:
            pair_files = dict(list(pair_files.items())[:max_pairs])
        if max_days is not None:
            pair_files = {pair: files[:max_days] for pair, files in pair_files.items()}
        groups[fcfg.ref] = dict(pair_files)
    return groups


def write_dataframe_hdf(df: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_hdf(path, key="data", mode="w")


def write_series_hdf(series: pd.Series, path: Path, key: str = "ncorr") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    series.to_hdf(path, key=key, mode="a")


def read_hdf_data(path: Path) -> pd.DataFrame:
    df = pd.read_hdf(path, key="data")
    df.index = pd.to_datetime(df.index)
    df.columns = df.columns.astype(float)
    return df.sort_index()


def stack_rows(rows: np.ndarray, cfg: RuntimeConfig) -> np.ndarray:
    if rows.shape[0] == 1:
        return rows[0].astype(np.float32)
    return msnoise_stack(
        rows,
        cfg.stack_method,
        cfg.pws_timegate,
        cfg.pws_power,
        cfg.cc_sampling_rate,
    ).astype(np.float32)


'''
run the hourly stack, MWCS, and DTT workflow:
'''

def run_hourly_stack(
    groups: Dict[int, Dict[Tuple[str, str], List[Path]]],
    filters: Sequence[FilterConfig],
    cfg: RuntimeConfig,
    stack_root: Path,
    component: str,
    min_windows_per_hour: int,
    reference_start: Optional[str],
    reference_end: Optional[str],
    force: bool,
) -> None:
    ref_start = pd.Timestamp(reference_start) if reference_start else None
    ref_end = pd.Timestamp(reference_end) if reference_end else None
    filter_map = {f.ref: f for f in filters}
    for filterid, pair_files in groups.items():
        if filterid not in filter_map:
            continue
        for pair_idx, ((sta1, sta2), files) in enumerate(pair_files.items(), start=1):
            pair = station_pair_name(sta1, sta2)
            print(
                f"[stack] filter={filterid:02d} pair={pair} files={len(files)} ({pair_idx}/{len(pair_files)})",
                flush=True,
            )
            ref_rows: List[np.ndarray] = []
            out_dir = stack_root / f"{filterid:02d}" / sta1 / sta2 / component
            for src in files:
                out_file = out_dir / src.name
                if out_file.exists() and not force:
                    hourly = read_hdf_data(out_file)
                else:
                    df = read_hdf_data(src)
                    rows: List[np.ndarray] = []
                    times: List[pd.Timestamp] = []
                    counts: List[int] = []
                    for hour, group in df.groupby(pd.Grouper(freq="1h")):
                        valid = group.dropna(how="all")
                        if len(valid) < min_windows_per_hour:
                            continue
                        arr = valid.to_numpy(dtype=float)
                        arr = arr[np.isfinite(arr).all(axis=1)]
                        if len(arr) < min_windows_per_hour:
                            continue
                        rows.append(stack_rows(arr, cfg))
                        times.append(pd.Timestamp(hour))
                        counts.append(len(arr))
                    if not rows:
                        continue
                    hourly = pd.DataFrame(rows, index=times, columns=df.columns)
                    write_dataframe_hdf(hourly, out_file)
                    write_series_hdf(pd.Series(counts, index=times, name="ncorr"), out_file)

                if ref_start is not None:
                    hourly = hourly[hourly.index >= ref_start]
                if ref_end is not None:
                    hourly = hourly[hourly.index <= ref_end]
                if len(hourly):
                    ref_rows.append(hourly.to_numpy(dtype=float))

            ref_file = out_dir / "REF.npy"
            if ref_rows and (force or not ref_file.exists()):
                all_ref_rows = np.vstack(ref_rows)
                all_ref_rows = all_ref_rows[np.isfinite(all_ref_rows).all(axis=1)]
                if len(all_ref_rows):
                    ref = stack_rows(all_ref_rows, cfg)
                    np.save(ref_file, ref)


def discover_stack_groups(
    stack_root: Path,
    filters: Sequence[FilterConfig],
    component: str,
    allowed_stations: Optional[set[str]],
    max_pairs: Optional[int],
    max_days: Optional[int],
) -> Dict[int, Dict[Tuple[str, str], List[Path]]]:
    groups: Dict[int, Dict[Tuple[str, str], List[Path]]] = {}
    for fcfg in filters:
        pattern = stack_root / f"{fcfg.ref:02d}" / "*" / "*" / component / "*.h5"
        pair_files: Dict[Tuple[str, str], List[Path]] = defaultdict(list)
        for filename in sorted(glob.glob(str(pattern))):
            path = Path(filename)
            sta2 = path.parent.parent.name
            sta1 = path.parent.parent.parent.name
            if allowed_stations and (sta1 not in allowed_stations or sta2 not in allowed_stations):
                continue
            pair_files[(sta1, sta2)].append(path)
        if max_pairs is not None:
            pair_files = dict(list(pair_files.items())[:max_pairs])
        if max_days is not None:
            pair_files = {pair: files[:max_days] for pair, files in pair_files.items()}
        groups[fcfg.ref] = dict(pair_files)
    return groups


def run_hourly_mwcs(
    groups: Dict[int, Dict[Tuple[str, str], List[Path]]],
    filters: Sequence[FilterConfig],
    cfg: RuntimeConfig,
    stack_root: Path,
    mwcs_root: Path,
    component: str,
    force: bool,
) -> None:
    filter_map = {f.ref: f for f in filters}
    for filterid, pair_files in groups.items():
        fcfg = filter_map[filterid]
        for pair_idx, ((sta1, sta2), files) in enumerate(pair_files.items(), start=1):
            pair = station_pair_name(sta1, sta2)
            ref_file = stack_root / f"{filterid:02d}" / sta1 / sta2 / component / "REF.npy"
            if not ref_file.exists():
                print(f"[mwcs] missing REF for filter={filterid:02d} pair={pair}; skipping", flush=True)
                continue
            ref = np.load(ref_file)
            out_dir = mwcs_root / f"{filterid:02d}" / sta1 / sta2 / component
            print(
                f"[mwcs] filter={filterid:02d} pair={pair} files={len(files)} ({pair_idx}/{len(pair_files)})",
                flush=True,
            )
            for stack_file in files:
                out_file = out_dir / f"{stack_file.stem}.csv.gz"
                if out_file.exists() and not force:
                    continue
                hourly = read_hdf_data(stack_file)
                rows = []
                for timestamp, current in hourly.iterrows():
                    cur = current.to_numpy(dtype=float)
                    if not np.isfinite(cur).all():
                        continue
                    output = msnoise_mwcs(
                        cur,
                        ref,
                        fcfg.mwcs_low,
                        fcfg.mwcs_high,
                        cfg.cc_sampling_rate,
                        -cfg.maxlag,
                        fcfg.mwcs_wlen,
                        fcfg.mwcs_step,
                    )
                    for lag_time, delay, error, coh in output:
                        rows.append(
                            {
                                "timestamp": timestamp,
                                "lag_time": lag_time,
                                "delay": delay,
                                "error": error,
                                "coh": coh,
                            }
                        )
                if rows:
                    out_dir.mkdir(parents=True, exist_ok=True)
                    pd.DataFrame(rows).to_csv(out_file, index=False)


def lag_mask(lags: np.ndarray, dist_km: float, cfg: RuntimeConfig) -> np.ndarray:
    if cfg.dtt_lag == "static":
        minlag = cfg.dtt_minlag
    else:
        minlag = dist_km / cfg.dtt_v if cfg.dtt_v else cfg.dtt_minlag
    left_min = -minlag - cfg.dtt_width
    left_max = -minlag
    right_min = minlag
    right_max = minlag + cfg.dtt_width
    if cfg.dtt_sides == "both":
        return ((lags >= left_min) & (lags <= left_max)) | ((lags >= right_min) & (lags <= right_max))
    if cfg.dtt_sides == "left":
        return (lags >= left_min) & (lags <= left_max)
    return (lags >= right_min) & (lags <= right_max)


def wavg_wstd(data: np.ndarray, errors: np.ndarray) -> Tuple[float, float]:
    if len(data) == 0:
        return np.nan, np.nan
    errors = errors.astype(float).copy()
    errors[errors == 0] = 1e-6
    w = 1.0 / errors
    wavg = (data * w).sum() / w.sum()
    n = len(np.nonzero(w)[0])
    if n <= 1:
        return float(wavg), float(errors[0])
    wstd = np.sqrt(np.sum(w * (data - wavg) ** 2) / ((n - 1) * np.sum(w) / n))
    return float(wavg), float(wstd)


def dtt_regression(
    masked: pd.DataFrame, cfg: RuntimeConfig, min_points: int
) -> Optional[Dict[str, float]]:
    valid = masked[
        (masked["coh"] >= cfg.dtt_mincoh)
        & (masked["error"] <= cfg.dtt_maxerr)
        & (masked["delay"].abs() <= cfg.dtt_maxdt)
    ].copy()
    if len(valid) < min_points:
        return None
    x = valid["lag_time"].to_numpy(dtype=float)
    y = valid["delay"].to_numpy(dtype=float)
    w = 1.0 / valid["error"].to_numpy(dtype=float)
    w[~np.isfinite(w)] = 1.0
    if len(y) < min_points:
        return None
    m, a, em, ea = linear_regression(x, y, w, intercept_origin=False)
    m0, em0 = linear_regression(x, y, w, intercept_origin=True)
    return {
        "M": float(m),
        "EM": float(em),
        "A": float(a),
        "EA": float(ea),
        "M0": float(m0),
        "EM0": float(em0),
        "dvv_percent": float(-100.0 * m),
        "dvv0_percent": float(-100.0 * m0),
        "npoints": int(len(valid)),
    }


def read_mwcs_pair_file(path: Path, pair: str) -> pd.DataFrame:
    df = pd.read_csv(path, parse_dates=["timestamp"])
    df["pair"] = pair
    return df


def run_hourly_dtt(
    filters: Sequence[FilterConfig],
    cfg: RuntimeConfig,
    mwcs_root: Path,
    dvv_root: Path,
    component: str,
    station_coords: Dict[str, Tuple[float, float]],
    include_all: bool,
    min_dtt_points: int,
    max_pairs: Optional[int],
    max_days: Optional[int],
) -> None:
    for fcfg in filters:
        pattern = mwcs_root / f"{fcfg.ref:02d}" / "*" / "*" / component / "*.csv.gz"
        pair_files: Dict[str, List[Path]] = defaultdict(list)
        for filename in sorted(glob.glob(str(pattern))):
            path = Path(filename)
            sta2 = path.parent.parent.name
            sta1 = path.parent.parent.parent.name
            if station_coords and (sta1 not in station_coords or sta2 not in station_coords):
                continue
            pair_files[station_pair_name(sta1, sta2)].append(path)
        if max_pairs is not None:
            pair_files = dict(list(pair_files.items())[:max_pairs])
        if max_days is not None:
            pair_files = {pair: files[:max_days] for pair, files in pair_files.items()}
        if not pair_files:
            print(f"[dtt] no MWCS files for filter={fcfg.ref:02d}", flush=True)
            continue

        rows: List[Dict[str, object]] = []
        all_records: List[pd.DataFrame] = []
        print(f"[dtt] filter={fcfg.ref:02d} pairs={len(pair_files)}", flush=True)
        for pair_idx, (pair, files) in enumerate(pair_files.items(), start=1):
            sta1, sta2 = split_pair_name(pair)
            dist_km = interstation_distance_km(sta1, sta2, station_coords)
            print(
                f"[dtt] filter={fcfg.ref:02d} pair={pair} files={len(files)} ({pair_idx}/{len(pair_files)})",
                flush=True,
            )
            for path in files:
                df = read_mwcs_pair_file(path, pair)
                if df.empty:
                    continue
                mask = lag_mask(df["lag_time"].to_numpy(dtype=float), dist_km, cfg)
                masked = df.copy()
                masked.loc[~mask, "error"] = 1.0
                masked.loc[~mask, "coh"] = 0.0
                if include_all:
                    valid_for_all = masked[
                        (masked["coh"] >= cfg.dtt_mincoh)
                        & (masked["error"] <= cfg.dtt_maxerr)
                        & (masked["delay"].abs() <= cfg.dtt_maxdt)
                    ][["timestamp", "lag_time", "delay", "error", "coh"]]
                    if not valid_for_all.empty:
                        all_records.append(valid_for_all)
                for timestamp, group in masked.groupby("timestamp"):
                    result = dtt_regression(group, cfg, min_dtt_points)
                    if result is None:
                        continue
                    result.update(
                        {
                            "timestamp": timestamp,
                            "pair": pair,
                            "distance_km": dist_km,
                            "filter": fcfg.ref,
                            "component": component,
                        }
                    )
                    rows.append(result)

        if include_all and all_records:
            all_df = pd.concat(all_records, ignore_index=True)
            for timestamp, time_group in all_df.groupby("timestamp"):
                agg_rows = []
                for lag_time, lag_group in time_group.groupby("lag_time"):
                    valid = lag_group[
                        (lag_group["coh"] >= cfg.dtt_mincoh)
                        & (lag_group["error"] <= cfg.dtt_maxerr)
                        & (lag_group["delay"].abs() <= cfg.dtt_maxdt)
                    ]
                    delay, error = wavg_wstd(
                        valid["delay"].to_numpy(dtype=float),
                        valid["error"].to_numpy(dtype=float),
                    )
                    if np.isfinite(delay) and np.isfinite(error):
                        agg_rows.append(
                            {
                                "lag_time": lag_time,
                                "delay": delay,
                                "error": error,
                                "coh": 1.0,
                            }
                        )
                if not agg_rows:
                    continue
                result = dtt_regression(pd.DataFrame(agg_rows), cfg, min_dtt_points)
                if result is None:
                    continue
                result.update(
                    {
                        "timestamp": timestamp,
                        "pair": "ALL",
                        "distance_km": np.nan,
                        "filter": fcfg.ref,
                        "component": component,
                    }
                )
                rows.append(result)

        if rows:
            out_dir = dvv_root / f"{fcfg.ref:02d}" / component
            out_dir.mkdir(parents=True, exist_ok=True)
            out_file = out_dir / "dvv.csv"
            out = pd.DataFrame(rows).sort_values(["timestamp", "pair"])
            out.to_csv(out_file, index=False)
            print(f"[dtt] wrote {out_file} rows={len(out)}", flush=True)


def export_hourly_txt(
    filters: Sequence[FilterConfig],
    dvv_root: Path,
    txt_root: Path,
    component: str,
    force: bool,
) -> None:
    columns = ["Date", "Pairs", "M", "EM", "A", "EA", "M0", "EM0"]
    for fcfg in filters:
        source = dvv_root / f"{fcfg.ref:02d}" / component / "dvv.csv"
        if not source.exists():
            print(f"[export] missing {source}; skipping", flush=True)
            continue
        df = pd.read_csv(source, parse_dates=["timestamp"])
        if df.empty:
            continue
        out_dir = txt_root / f"{fcfg.ref:02d}" / "001_HOURS" / component
        out_dir.mkdir(parents=True, exist_ok=True)
        wrote = 0
        for timestamp, group in df.groupby("timestamp"):
            ts = pd.Timestamp(timestamp)
            out_file = out_dir / f"{ts.strftime('%Y-%m-%dT%H-%M-%S')}.txt"
            if out_file.exists() and not force:
                continue
            out = pd.DataFrame(
                {
                    "Date": ts.strftime("%Y-%m-%d %H:%M:%S"),
                    "Pairs": group["pair"],
                    "M": group["M"],
                    "EM": group["EM"],
                    "A": group["A"],
                    "EA": group["EA"],
                    "M0": group["M0"],
                    "EM0": group["EM0"],
                }
            )
            out = out.sort_values("Pairs")
            out.to_csv(out_file, index=False, columns=columns)
            wrote += 1
        print(f"[export] filter={fcfg.ref:02d} wrote {wrote} hourly txt files to {out_dir}", flush=True)


def main() -> None:
    args = parse_args()
    cc_root = Path(args.cc_root)
    stack_root = Path(args.stack_root)
    mwcs_root = Path(args.mwcs_root)
    dvv_root = Path(args.dvv_root)
    txt_root = Path(args.txt_root)
    with connect(args.db) as conn:
        cfg = load_runtime_config(conn)
        filters = load_filters(conn, args.filters)
        station_coords = load_station_coordinates(conn)

    if args.stage in ("all", "stack"):
        allowed_stations = set(station_coords)
        cc_groups = discover_cc_groups(
            cc_root, filters, args.component, allowed_stations, args.max_pairs, args.max_days
        )
        run_hourly_stack(
            cc_groups,
            filters,
            cfg,
            stack_root,
            args.component,
            args.min_windows_per_hour,
            args.reference_start,
            args.reference_end,
            args.force,
        )

    if args.stage in ("all", "mwcs"):
        allowed_stations = set(station_coords)
        stack_groups = discover_stack_groups(
            stack_root, filters, args.component, allowed_stations, args.max_pairs, args.max_days
        )
        run_hourly_mwcs(stack_groups, filters, cfg, stack_root, mwcs_root, args.component, args.force)

    if args.stage in ("all", "dtt"):
        run_hourly_dtt(
            filters,
            cfg,
            mwcs_root,
            dvv_root,
            args.component,
            station_coords,
            include_all=args.include_all,
            min_dtt_points=args.min_dtt_points,
            max_pairs=args.max_pairs,
            max_days=args.max_days,
        )

    if args.stage in ("all", "dtt", "export") and args.export_txt:
        export_hourly_txt(filters, dvv_root, txt_root, args.component, args.force)


if __name__ == "__main__":
    main()
