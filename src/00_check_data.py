#!/usr/bin/env python3
"""Query FDSN station availability and build an interactive map.

This script does not read any pre-generated availability CSV. It queries FDSN
station services directly, merges duplicate station/channel metadata from
multiple data centers, keeps distinct availability windows, and writes an
interactive HTML map where clicking a station/node shows its availability.
"""

from __future__ import annotations

import argparse
import csv
import html
import json
import math
import os
import subprocess
import sys
import urllib.parse
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Iterable

try:
    import folium
    from folium.plugins import MarkerCluster
    from branca.element import Element
    from obspy import UTCDateTime
    from obspy.clients.fdsn import RoutingClient
    from obspy.clients.fdsn.header import FDSNNoDataException
except ImportError as exc:  # pragma: no cover - this is a user-facing guard.
    missing = exc.name or "required package"
    print(
        f"Missing Python package: {missing}\n\n"
        "Use an environment with ObsPy and Folium, for example:\n"
        "  conda run -n msnoise-hm python src/00_check_data.py\n\n"
        "Or create one:\n"
        "  conda create -n seismic-precheck -c conda-forge python=3.11 obspy folium pandas\n"
        "  conda run -n seismic-precheck python src/00_check_data.py",
        file=sys.stderr,
    )
    raise SystemExit(2)


DEFAULT_CLIENTS = [
    "iris-federator",
    "eida-routing",
    "IRIS",
    "GFZ",
    "GEOFON",
    "ORFEUS",
    "RESIF",
    "ETH",
    "INGV",
    "NOA",
    "BGR",
    "KNMI",
    "ODC",
    "IPGP",
    "UIB-NORSAR",
]
EIDA_CLIENTS = [
    "eida-routing",
    "ORFEUS",
    "GFZ",
    "GEOFON",
    "RESIF",
    "ETH",
    "INGV",
    "NOA",
    "BGR",
    "KNMI",
    "ODC",
    "IPGP",
    "UIB-NORSAR",
]

DEFAULT_DAS_NETWORKS = {"1D", "5J", "ZH"}
DEFAULT_DAS_CHANNELS = {"HSF", "MSF"}
DEFAULT_OUTPUT_HTML = "pre_check_seismic_data.html"
DEFAULT_OUTPUT_CSV = "pre_check_seismic_segments.csv"
DEFAULT_OBSPY_ROUTERS = ["iris-federator", "eida-routing"]
SERVICE_ENDPOINTS = {
    "iris-federator": "https://service.iris.edu/fdsnws/station/1/query",
    "IRIS": "https://service.iris.edu/fdsnws/station/1/query",
    "eida-routing": "https://www.orfeus-eu.org/fdsnws/station/1/query",
    "ORFEUS": "https://www.orfeus-eu.org/fdsnws/station/1/query",
    "ODC": "https://www.orfeus-eu.org/fdsnws/station/1/query",
    "GFZ": "https://geofon.gfz.de/fdsnws/station/1/query",
    "GEOFON": "https://geofon.gfz.de/fdsnws/station/1/query",
    "RESIF": "https://ws.resif.fr/fdsnws/station/1/query",
    "ETH": "https://eida.ethz.ch/fdsnws/station/1/query",
    "INGV": "https://webservices.ingv.it/fdsnws/station/1/query",
    "NOA": "https://eida.gein.noa.gr/fdsnws/station/1/query",
    "BGR": "https://eida.bgr.de/fdsnws/station/1/query",
    "KNMI": "https://rdsa.knmi.nl/fdsnws/station/1/query",
    "IPGP": "https://ws.ipgp.fr/fdsnws/station/1/query",
    "UIB-NORSAR": "https://eida.geo.uib.no/fdsnws/station/1/query",
}


def selector_default(value, fallback: str) -> str:
    if value is None:
        return fallback
    if isinstance(value, list):
        return ",".join(str(item).strip() for item in value if str(item).strip())
    return str(value)


def list_default(value, fallback: list[str]) -> list[str]:
    if value is None:
        return fallback
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return [item.strip() for item in str(value).split(",") if item.strip()]


def bool_default(value, fallback: bool) -> bool:
    if value is None:
        return fallback
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "t", "yes", "y", "on"}


def load_config_defaults(config_path: str) -> dict[str, object]:
    path = Path(config_path).expanduser()
    if not path.exists():
        return {}
    try:
        with path.open("r", encoding="utf-8") as handle:
            config = json.load(handle)
    except json.JSONDecodeError as exc:
        raise argparse.ArgumentTypeError(f"invalid JSON in {path}: {exc}") from exc

    search = config.get("search_criteria", {})
    if not isinstance(search, dict):
        return {}

    precheck = config.get("precheck", {})
    if not isinstance(precheck, dict):
        precheck = {}

    defaults: dict[str, object] = {}
    if search.get("start_date"):
        defaults["start"] = str(search["start_date"])
    if search.get("end_date"):
        defaults["end"] = str(search["end_date"])

    region = search.get("region", {})
    if isinstance(region, dict) and all(
        key in region for key in ("min_lon", "max_lon", "min_lat", "max_lat")
    ):
        defaults["bbox"] = (
            f"{region['min_lon']},{region['max_lon']},"
            f"{region['min_lat']},{region['max_lat']}"
        )

    defaults["clients"] = selector_default(search.get("clients"), "auto")
    defaults["networks"] = selector_default(search.get("networks"), "*")
    defaults["stations"] = selector_default(search.get("stations"), "*")
    defaults["locations"] = selector_default(search.get("locations"), "*")
    defaults["channels"] = selector_default(search.get("channels"), "*")
    if search.get("waveform_timeout") is not None:
        defaults["timeout"] = int(search["waveform_timeout"])
    elif search.get("timeout") is not None:
        defaults["timeout"] = int(search["timeout"])
    defaults["include_restricted"] = bool_default(
        search.get("include_restricted"),
        bool_default(precheck.get("include_restricted"), False),
    )
    defaults["workers"] = int(precheck.get("workers", 6))
    defaults["output"] = str(precheck.get("output", DEFAULT_OUTPUT_HTML))
    defaults["csv"] = str(precheck.get("csv", DEFAULT_OUTPUT_CSV))
    defaults["basemap"] = str(precheck.get("basemap", "nolabels"))
    defaults["max_popup_rows"] = int(precheck.get("max_popup_rows", 90))
    defaults["obspy_double_check"] = bool_default(precheck.get("obspy_double_check"), True)
    defaults["obspy_routers"] = selector_default(
        precheck.get("obspy_routers", DEFAULT_OBSPY_ROUTERS),
        ",".join(DEFAULT_OBSPY_ROUTERS),
    )
    return defaults


@dataclass(frozen=True)
class SegmentKey:
    network: str
    station: str
    location: str
    channel: str
    lat: str
    lon: str
    start: str
    end: str


@dataclass
class Segment:
    network: str
    station: str
    location: str
    channel: str
    lat: float
    lon: float
    elev_m: float | None
    sample_rate_hz: float | None
    start: datetime
    end: datetime
    sources: set[str] = field(default_factory=set)
    sensor: str = ""

    @property
    def key(self) -> SegmentKey:
        return SegmentKey(
            self.network,
            self.station,
            self.location or "--",
            self.channel,
            f"{self.lat:.5f}",
            f"{self.lon:.5f}",
            iso_day(self.start),
            iso_day(self.end),
        )


@dataclass
class StationGroup:
    group_id: str
    network: str
    station: str
    label: str
    kind: str
    lat_values: list[float] = field(default_factory=list)
    lon_values: list[float] = field(default_factory=list)
    nodes: set[str] = field(default_factory=set)
    channels: set[str] = field(default_factory=set)
    locations: set[str] = field(default_factory=set)
    sources: set[str] = field(default_factory=set)
    sensors: set[str] = field(default_factory=set)
    intervals: list[tuple[datetime, datetime]] = field(default_factory=list)
    details: list[Segment] = field(default_factory=list)
    min_lat: float = math.inf
    max_lat: float = -math.inf
    min_lon: float = math.inf
    max_lon: float = -math.inf

    def add(self, seg: Segment) -> None:
        self.lat_values.append(seg.lat)
        self.lon_values.append(seg.lon)
        self.nodes.add(seg.station)
        self.channels.add(seg.channel)
        self.locations.add(seg.location or "--")
        self.sources.update(seg.sources)
        if seg.sensor:
            self.sensors.add(seg.sensor)
        self.intervals.append((seg.start, seg.end))
        self.details.append(seg)
        self.min_lat = min(self.min_lat, seg.lat)
        self.max_lat = max(self.max_lat, seg.lat)
        self.min_lon = min(self.min_lon, seg.lon)
        self.max_lon = max(self.max_lon, seg.lon)

    @property
    def lat(self) -> float:
        return sum(self.lat_values) / len(self.lat_values)

    @property
    def lon(self) -> float:
        return sum(self.lon_values) / len(self.lon_values)

    @property
    def merged_intervals(self) -> list[tuple[datetime, datetime]]:
        return merge_intervals(self.intervals)


def parse_date(value: str, label: str) -> datetime:
    try:
        return datetime.strptime(value, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"{label} must be YYYY-MM-DD, got {value!r}") from exc


def parse_bbox(value: str) -> tuple[float, float, float, float]:
    parts = [item.strip() for item in value.split(",")]
    if len(parts) != 4:
        raise argparse.ArgumentTypeError("bbox must be min_lon,max_lon,min_lat,max_lat")
    try:
        min_lon, max_lon, min_lat, max_lat = [float(item) for item in parts]
    except ValueError as exc:
        raise argparse.ArgumentTypeError("bbox values must be numeric") from exc
    if min_lon >= max_lon or min_lat >= max_lat:
        raise argparse.ArgumentTypeError("bbox min values must be smaller than max values")
    return min_lon, max_lon, min_lat, max_lat


def resolve_bbox(args: argparse.Namespace) -> tuple[float, float, float, float]:
    explicit = [args.min_lon, args.max_lon, args.min_lat, args.max_lat]
    if any(value is not None for value in explicit):
        if not all(value is not None for value in explicit):
            raise argparse.ArgumentTypeError(
                "use all four explicit bounds: --min-lon --max-lon --min-lat --max-lat"
            )
        min_lon = float(args.min_lon)
        max_lon = float(args.max_lon)
        min_lat = float(args.min_lat)
        max_lat = float(args.max_lat)
        if min_lon >= max_lon or min_lat >= max_lat:
            raise argparse.ArgumentTypeError("bbox min values must be smaller than max values")
        return min_lon, max_lon, min_lat, max_lat
    return parse_bbox(args.bbox)


def to_datetime(value: UTCDateTime | None, fallback: datetime) -> datetime:
    if value is None:
        return fallback
    return value.datetime.replace(tzinfo=timezone.utc)


def args_date(value: datetime) -> str:
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")


def parse_text_time(value: str) -> datetime:
    cleaned = value.strip()
    for fmt in ("%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(cleaned, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    raise ValueError(f"unsupported FDSN time {value!r}")


def iso_day(value: datetime) -> str:
    return value.astimezone(timezone.utc).date().isoformat()


def clean_float(value: float | None) -> str:
    if value is None:
        return ""
    return f"{value:.6g}"


def get_sensor_text(channel) -> str:
    sensor = getattr(channel, "sensor", None)
    if sensor is None:
        return ""
    parts = [
        getattr(sensor, "type", "") or "",
        getattr(sensor, "description", "") or "",
        getattr(sensor, "manufacturer", "") or "",
        getattr(sensor, "model", "") or "",
    ]
    return " ".join(part.strip() for part in parts if part and part.strip())


def station_service_url(name: str) -> str:
    if name in SERVICE_ENDPOINTS:
        return SERVICE_ENDPOINTS[name]
    if name.startswith("http://") or name.startswith("https://"):
        return name.rstrip("/") + "/fdsnws/station/1/query"
    return name


def cache_path_for(service_name: str, url: str) -> Path | None:
    cache_dir = os.environ.get("PRECHECK_CACHE_DIR")
    if not cache_dir:
        return None
    safe_name = service_name.replace("/", "_").replace(":", "_")
    return Path(cache_dir) / f"{safe_name}.txt"


def query_one_service(
    service_name: str,
    start: datetime,
    end: datetime,
    bbox: tuple[float, float, float, float],
    channels: str,
    networks: str,
    stations: str,
    locations: str,
    timeout: int,
    include_restricted: bool,
) -> tuple[str, list[Segment], str | None]:
    min_lon, max_lon, min_lat, max_lat = bbox
    try:
        params = {
            "network": networks,
            "station": stations,
            "location": locations,
            "channel": channels,
            "starttime": args_date(start),
            "endtime": args_date(end),
            "minlatitude": f"{min_lat:.6f}",
            "maxlatitude": f"{max_lat:.6f}",
            "minlongitude": f"{min_lon:.6f}",
            "maxlongitude": f"{max_lon:.6f}",
            "level": "channel",
            "format": "text",
            "nodata": "404",
            "includerestricted": "true" if include_restricted else "false",
        }
        url = station_service_url(service_name) + "?" + urllib.parse.urlencode(params)
        cache_path = cache_path_for(service_name, url)
        if cache_path and cache_path.exists():
            payload = cache_path.read_text(encoding="utf-8")
        else:
            result = subprocess.run(
                ["curl", "-L", "--silent", "--show-error", "--max-time", str(timeout), url],
                check=True,
                capture_output=True,
                text=True,
            )
            payload = result.stdout
    except Exception as exc:  # FDSN servers often differ; keep scanning others.
        return service_name, [], str(exc).splitlines()[0]

    rows: list[Segment] = []
    for raw_line in payload.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        parts = [item.strip() for item in line.split("|")]
        if len(parts) < 17:
            continue
        try:
            network = parts[0]
            station = parts[1]
            location = parts[2] or "--"
            channel = parts[3]
            lat = float(parts[4])
            lon = float(parts[5])
            elev_m = float(parts[6]) if parts[6] else None
            sensor = parts[10]
            sample_rate_hz = float(parts[14]) if parts[14] else None
            seg_start = max(parse_text_time(parts[15]), start)
            raw_seg_end = parse_text_time(parts[16]) if parts[16] else end
            seg_end = min(raw_seg_end, end)
        except (ValueError, IndexError):
            continue

        if not (min_lat <= lat <= max_lat and min_lon <= lon <= max_lon):
            continue
        if seg_start > end or seg_end < start or seg_start > seg_end:
            continue

        rows.append(
            Segment(
                network=network,
                station=station,
                location=location,
                channel=channel,
                lat=lat,
                lon=lon,
                elev_m=elev_m,
                sample_rate_hz=sample_rate_hz,
                start=seg_start,
                end=seg_end,
                sources={service_name},
                sensor=sensor,
            )
        )
    return service_name, rows, None


def inventory_sources(router_name: str, inventory) -> set[str]:
    if router_name == "eida-routing":
        return {f"obspy-{router_name}", *EIDA_CLIENTS}
    if router_name == "iris-federator":
        return {f"obspy-{router_name}", "IRIS"}
    return {f"obspy-{router_name}"}


def inventory_segments(
    router_name: str,
    inventory,
    start: datetime,
    end: datetime,
    bbox: tuple[float, float, float, float],
) -> list[Segment]:
    min_lon, max_lon, min_lat, max_lat = bbox
    sources = inventory_sources(router_name, inventory)
    rows: list[Segment] = []
    for network in inventory:
        for station in network:
            for channel in station.channels:
                lat = float(getattr(channel, "latitude", station.latitude))
                lon = float(getattr(channel, "longitude", station.longitude))
                if not (min_lat <= lat <= max_lat and min_lon <= lon <= max_lon):
                    continue
                chan_start = to_datetime(getattr(channel, "start_date", None), start)
                chan_end = to_datetime(getattr(channel, "end_date", None), end)
                seg_start = max(chan_start, start)
                seg_end = min(chan_end, end)
                if seg_start > end or seg_end < start or seg_start > seg_end:
                    continue
                rows.append(
                    Segment(
                        network=network.code,
                        station=station.code,
                        location=channel.location_code or "--",
                        channel=channel.code,
                        lat=lat,
                        lon=lon,
                        elev_m=float(getattr(channel, "elevation", station.elevation))
                        if getattr(channel, "elevation", None) is not None
                        else None,
                        sample_rate_hz=float(channel.sample_rate)
                        if getattr(channel, "sample_rate", None) is not None
                        else None,
                        start=seg_start,
                        end=seg_end,
                        sources=set(sources),
                        sensor=get_sensor_text(channel),
                    )
                )
    return rows


def query_one_obspy_router(
    router_name: str,
    start: datetime,
    end: datetime,
    bbox: tuple[float, float, float, float],
    channels: str,
    networks: str,
    stations: str,
    locations: str,
    include_restricted: bool,
) -> tuple[str, list[Segment], str | None]:
    min_lon, max_lon, min_lat, max_lat = bbox
    try:
        client = RoutingClient(router_name)
        inventory = client.get_stations(
            network=networks,
            station=stations,
            location=locations,
            channel=channels,
            starttime=UTCDateTime(start),
            endtime=UTCDateTime(end),
            minlatitude=min_lat,
            maxlatitude=max_lat,
            minlongitude=min_lon,
            maxlongitude=max_lon,
            level="channel",
            includerestricted=include_restricted,
        )
        return router_name, inventory_segments(router_name, inventory, start, end, bbox), None
    except FDSNNoDataException:
        return router_name, [], None
    except Exception as exc:
        return router_name, [], str(exc).splitlines()[0]


def merge_segment(merged: dict[SegmentKey, Segment], row: Segment, update_sources: bool = True) -> bool:
    key = row.key
    if key in merged:
        if update_sources:
            merged[key].sources.update(row.sources)
        if not merged[key].sensor and row.sensor:
            merged[key].sensor = row.sensor
        return False
    merged[key] = row
    return True


def query_services(args: argparse.Namespace) -> tuple[list[Segment], list[tuple[str, str | None, int]]]:
    services = parse_clients(args.clients)
    start = parse_date(args.start, "start")
    end = parse_date(args.end, "end")
    bbox = resolve_bbox(args)

    print(f"Time: {args.start} to {args.end}")
    print(f"BBox: lon {bbox[0]} to {bbox[1]}, lat {bbox[2]} to {bbox[3]}")
    print(f"Networks: {args.networks}")
    print(f"Stations: {args.stations}")
    print(f"Locations: {args.locations}")
    print(f"Channels: {args.channels}")
    print(f"Services: {', '.join(services)}")
    if args.obspy_double_check:
        print(f"ObsPy double check routers: {args.obspy_routers}")

    merged: dict[SegmentKey, Segment] = {}
    service_status: list[tuple[str, str | None, int]] = []

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = [
            pool.submit(
                query_one_service,
                service,
                start,
                end,
                bbox,
                args.channels,
                args.networks,
                args.stations,
                args.locations,
                args.timeout,
                args.include_restricted,
            )
            for service in services
        ]
        for future in as_completed(futures):
            service, rows, error = future.result()
            service_status.append((service, error, len(rows)))
            if error:
                print(f"[{service}] skipped/error: {error}")
                continue
            print(f"[{service}] channel segments: {len(rows)}")
            for row in rows:
                merge_segment(merged, row)

    if args.obspy_double_check:
        routers = [item.strip() for item in args.obspy_routers.split(",") if item.strip()]
        for router in routers:
            router_name, rows, error = query_one_obspy_router(
                router,
                start,
                end,
                bbox,
                args.channels,
                args.networks,
                args.stations,
                args.locations,
                args.include_restricted,
            )
            service_status.append((f"obspy:{router_name}", error, len(rows)))
            if error:
                print(f"[obspy:{router_name}] skipped/error: {error}")
                continue
            added = sum(1 for row in rows if merge_segment(merged, row, update_sources=False))
            print(f"[obspy:{router_name}] channel segments: {len(rows)} ({added} added)")

    segments = sorted(
        merged.values(),
        key=lambda x: (x.network, x.station, x.location, x.channel, x.start, x.end),
    )
    print(f"Merged unique station/channel windows: {len(segments)}")
    return segments, sorted(service_status)


def parse_clients(raw: str) -> list[str]:
    if raw.strip().lower() in {"auto", "default"}:
        return DEFAULT_CLIENTS
    if raw.strip().lower() in {"routing", "routers"}:
        return ["iris-federator", "eida-routing"]
    clients = []
    for item in (part.strip() for part in raw.split(",")):
        if not item:
            continue
        if item.upper() == "EIDA":
            clients.extend(EIDA_CLIENTS)
        else:
            clients.append(item)
    if not clients:
        raise argparse.ArgumentTypeError("at least one client/service is required")
    return list(dict.fromkeys(clients))


def is_das_segment(seg: Segment, das_networks: set[str], das_channels: set[str]) -> bool:
    sensor = seg.sensor.lower()
    channel = seg.channel.upper()
    if seg.network in das_networks:
        return True
    if channel in das_channels:
        return True
    return "das" in sensor or "distributed acoustic" in sensor or "fiber" in sensor


def is_synthetic_segment(seg: Segment) -> bool:
    sensor = seg.sensor.lower()
    return seg.network == "SY" or "synthetic" in sensor


def coordinate_key(seg: Segment) -> str:
    return f"{seg.lat:.4f},{seg.lon:.4f}"


def segment_kind(seg: Segment, das_networks: set[str], das_channels: set[str]) -> str:
    if is_das_segment(seg, das_networks, das_channels):
        return "DAS"
    if is_synthetic_segment(seg):
        return "synthetic"
    return "seismic"


def group_segments(
    segments: Iterable[Segment],
    das_networks: set[str],
    das_channels: set[str],
    collapse_das: bool,
    split_station_moves: bool,
) -> list[StationGroup]:
    segments = list(segments)
    station_positions: dict[tuple[str, str, str], set[str]] = defaultdict(set)
    for seg in segments:
        kind = segment_kind(seg, das_networks, das_channels)
        station_positions[(kind, seg.network, seg.station)].add(coordinate_key(seg))

    groups: dict[str, StationGroup] = {}
    for seg in segments:
        kind = segment_kind(seg, das_networks, das_channels)
        positions = station_positions[(kind, seg.network, seg.station)]
        needs_position_suffix = split_station_moves and len(positions) > 1

        if kind == "DAS" and collapse_das:
            group_id = f"{seg.network}.DAS"
            label = f"{seg.network} DAS"
            station = "DAS"
        elif needs_position_suffix:
            coord = coordinate_key(seg)
            group_id = f"{seg.network}.{seg.station}@{coord}"
            label = f"{seg.network}.{seg.station} @ {coord}"
            station = seg.station
        else:
            group_id = f"{seg.network}.{seg.station}"
            label = f"{seg.network}.{seg.station}"
            station = seg.station

        if group_id not in groups:
            groups[group_id] = StationGroup(
                group_id=group_id,
                network=seg.network,
                station=station,
                label=label,
                kind=kind,
            )
        groups[group_id].add(seg)

    type_order = {"DAS": 0, "seismic": 1, "synthetic": 2}
    return sorted(groups.values(), key=lambda g: (type_order.get(g.kind, 9), g.network, g.station))


def merge_intervals(intervals: Iterable[tuple[datetime, datetime]]) -> list[tuple[datetime, datetime]]:
    sorted_intervals = sorted(intervals)
    if not sorted_intervals:
        return []
    merged = [sorted_intervals[0]]
    for start, end in sorted_intervals[1:]:
        prev_start, prev_end = merged[-1]
        if start <= prev_end:
            merged[-1] = (prev_start, max(prev_end, end))
        else:
            merged.append((start, end))
    return merged


def availability_html(group: StationGroup, start: datetime, end: datetime) -> str:
    total_seconds = max((end - start).total_seconds(), 1.0)
    bars = []
    for seg_start, seg_end in group.merged_intervals:
        left = max(0.0, (seg_start - start).total_seconds() / total_seconds * 100.0)
        width = max(0.25, (seg_end - seg_start).total_seconds() / total_seconds * 100.0)
        bars.append(
            f'<span class="avail-segment" style="left:{left:.3f}%;width:{width:.3f}%;" '
            f'title="{iso_day(seg_start)} to {iso_day(seg_end)}"></span>'
        )
    ticks = (
        f'<div class="avail-ticks"><span>{html.escape(iso_day(start))}</span>'
        f'<span>{html.escape(iso_day(end))}</span></div>'
    )
    return f'<div class="avail-bar">{"".join(bars)}</div>{ticks}'


def details_table(group: StationGroup, max_rows: int) -> str:
    by_detail: dict[tuple[str, str, str, str, str], dict[str, object]] = {}
    for seg in group.details:
        if group.kind == "DAS":
            key = (
                seg.channel,
                iso_day(seg.start),
                iso_day(seg.end),
                clean_float(seg.sample_rate_hz),
                ",".join(sorted(seg.sources)),
            )
        else:
            key = (
                f"{seg.location}.{seg.channel}",
                iso_day(seg.start),
                iso_day(seg.end),
                clean_float(seg.sample_rate_hz),
                ",".join(sorted(seg.sources)),
            )
        if key not in by_detail:
            by_detail[key] = {"nodes": set(), "sensor": seg.sensor}
        by_detail[key]["nodes"].add(seg.station)

    rows = []
    for key, value in sorted(by_detail.items()):
        code, start, end, sample_rate, sources = key
        node_count = len(value["nodes"])
        rows.append(
            "<tr>"
            f"<td>{html.escape(code)}</td>"
            f"<td>{html.escape(start)}</td>"
            f"<td>{html.escape(end)}</td>"
            f"<td>{html.escape(sample_rate)}</td>"
            f"<td>{node_count if group.kind == 'DAS' else ''}</td>"
            f"<td>{html.escape(sources)}</td>"
            "</tr>"
        )
    truncated = ""
    if len(rows) > max_rows:
        truncated = f"<p>Showing first {max_rows} of {len(rows)} channel windows.</p>"
        rows = rows[:max_rows]
    return (
        f"{truncated}<table><thead><tr>"
        "<th>Loc.Channel</th><th>Start</th><th>End</th><th>Hz</th><th>DAS nodes</th><th>Sources</th>"
        f"</tr></thead><tbody>{''.join(rows)}</tbody></table>"
    )


def popup_html(group: StationGroup, start: datetime, end: datetime, max_rows: int) -> str:
    intervals = group.merged_intervals
    extent = ""
    if group.kind == "DAS" and len(group.nodes) > 1:
        extent = (
            f"<p><b>DAS nodes:</b> {len(group.nodes)}<br>"
            f"<b>Extent:</b> lon {group.min_lon:.4f} to {group.max_lon:.4f}, "
            f"lat {group.min_lat:.4f} to {group.max_lat:.4f}</p>"
        )
    summary = (
        f"<h3>{html.escape(group.label)}</h3>"
        f"<p><b>Type:</b> {html.escape(group.kind)}<br>"
        f"<b>Network:</b> {html.escape(group.network)}<br>"
        f"<b>Channels:</b> {html.escape(', '.join(sorted(group.channels)))}<br>"
        f"<b>Sources:</b> {html.escape(', '.join(sorted(group.sources)))}<br>"
        f"<b>Availability:</b> {html.escape(iso_day(intervals[0][0]))} to "
        f"{html.escape(iso_day(intervals[-1][1]))}</p>"
        f"{extent}"
        f"{availability_html(group, start, end)}"
        f"{details_table(group, max_rows)}"
    )
    return f'<div class="popup">{summary}</div>'


def color_for(kind: str) -> str:
    return {"DAS": "#00856f", "seismic": "#1f77b4", "synthetic": "#6f6b00"}.get(kind, "#444444")


def basemap_tiles(style: str) -> tuple[str, str]:
    if style == "osm":
        return "OpenStreetMap", ""
    if style == "dark":
        return (
            "https://{s}.basemaps.cartocdn.com/dark_nolabels/{z}/{x}/{y}{r}.png",
            "&copy; OpenStreetMap contributors &copy; CARTO",
        )
    return (
        "https://{s}.basemaps.cartocdn.com/light_nolabels/{z}/{x}/{y}{r}.png",
        "&copy; OpenStreetMap contributors &copy; CARTO",
    )


def write_csv(segments: list[Segment], path: Path) -> None:
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(
            [
                "network",
                "station",
                "location",
                "channel",
                "latitude",
                "longitude",
                "elevation_m",
                "sample_rate_hz",
                "start",
                "end",
                "sources",
                "sensor",
            ]
        )
        for seg in segments:
            writer.writerow(
                [
                    seg.network,
                    seg.station,
                    seg.location,
                    seg.channel,
                    f"{seg.lat:.7f}",
                    f"{seg.lon:.7f}",
                    clean_float(seg.elev_m),
                    clean_float(seg.sample_rate_hz),
                    iso_day(seg.start),
                    iso_day(seg.end),
                    ",".join(sorted(seg.sources)),
                    seg.sensor,
                ]
            )


def service_summary_html(service_status: list[tuple[str, str | None, int]]) -> str:
    rows = []
    for service, error, count in service_status:
        status = "ok" if error is None else f"error: {error}"
        rows.append(
            "<tr>"
            f"<td>{html.escape(service)}</td>"
            f"<td>{count}</td>"
            f"<td>{html.escape(status)}</td>"
            "</tr>"
        )
    return (
        "<details class='service-summary'><summary>FDSN query status</summary>"
        "<table><thead><tr><th>Service</th><th>Raw channel windows</th><th>Status</th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody></table></details>"
    )


def write_map(
    groups: list[StationGroup],
    service_status: list[tuple[str, str | None, int]],
    output: Path,
    start: datetime,
    end: datetime,
    bbox: tuple[float, float, float, float],
    max_popup_rows: int,
    basemap: str,
) -> None:
    min_lon, max_lon, min_lat, max_lat = bbox
    center = [(min_lat + max_lat) / 2.0, (min_lon + max_lon) / 2.0]
    tiles, attr = basemap_tiles(basemap)
    if attr:
        fmap = folium.Map(location=center, zoom_start=9, control_scale=True, tiles=tiles, attr=attr)
    else:
        fmap = folium.Map(location=center, zoom_start=9, control_scale=True, tiles=tiles)
    fmap.fit_bounds([[min_lat, min_lon], [max_lat, max_lon]])
    folium.Rectangle(
        bounds=[[min_lat, min_lon], [max_lat, max_lon]],
        color="#d43f3a",
        weight=2,
        fill=False,
        tooltip="query bbox",
    ).add_to(fmap)

    clusters = {
        "DAS": MarkerCluster(name="DAS groups", show=True).add_to(fmap),
        "seismic": MarkerCluster(name="seismic stations", show=True).add_to(fmap),
        "synthetic": MarkerCluster(name="synthetic stations", show=False).add_to(fmap),
    }

    for group in groups:
        popup = folium.Popup(popup_html(group, start, end, max_popup_rows), max_width=820)
        radius = 9 if group.kind == "DAS" else 5
        marker = folium.CircleMarker(
            location=[group.lat, group.lon],
            radius=radius,
            weight=2,
            color=color_for(group.kind),
            fill=True,
            fill_color=color_for(group.kind),
            fill_opacity=0.82,
            tooltip=group.label,
            popup=popup,
        )
        marker.add_to(clusters.get(group.kind, fmap))

    folium.LayerControl(collapsed=False).add_to(fmap)
    counts = defaultdict(int)
    for group in groups:
        counts[group.kind] += 1
    networks = ", ".join(sorted({group.network for group in groups}))
    summary = (
        f"<h1>Station availability pre-check</h1>"
        f"<p><b>Time:</b> {html.escape(iso_day(start))} to {html.escape(iso_day(end))} | "
        f"<b>BBox:</b> lon {min_lon:g} to {max_lon:g}, lat {min_lat:g} to {max_lat:g}</p>"
        f"<p><b>Station groups:</b> {len(groups)} "
        f"({counts['seismic']} seismic, {counts['DAS']} DAS, {counts['synthetic']} synthetic). "
        f"<b>Networks:</b> {html.escape(networks)}</p>"
        "<p>Click a marker to see merged availability windows and channel-level details. "
        "DAS channels are shown as node markers unless --collapse-das is used. "
        "Stations with multiple coordinates are split into separate markers.</p>"
        f"{service_summary_html(service_status)}"
    )
    root = fmap.get_root()
    root.html.add_child(Element(CSS_BLOCK))
    root.html.add_child(Element(f"<section class='precheck-summary'>{summary}</section>"))
    fmap.save(str(output))


CSS_BLOCK = """
<style>
  body {
    margin: 0;
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    color: #182026;
  }
  .precheck-summary {
    position: relative;
    z-index: 9999;
    max-width: 1180px;
    margin: 12px auto;
    padding: 14px 18px;
    background: rgba(255, 255, 255, 0.94);
    border: 1px solid #d7dcda;
    border-radius: 8px;
    box-shadow: 0 2px 12px rgba(0, 0, 0, 0.08);
  }
  .precheck-summary h1 {
    margin: 0 0 6px;
    font-size: 22px;
  }
  .precheck-summary p {
    margin: 6px 0;
  }
  .service-summary {
    margin-top: 8px;
  }
  .popup {
    width: 760px;
    max-width: 78vw;
    font-size: 13px;
  }
  .popup h3 {
    margin: 0 0 8px;
    font-size: 18px;
  }
  .popup p {
    margin: 6px 0 9px;
  }
  .avail-bar {
    position: relative;
    height: 16px;
    margin: 8px 0 2px;
    border: 1px solid #cfd6d5;
    background: #f3f3ef;
    border-radius: 4px;
    overflow: hidden;
  }
  .avail-segment {
    position: absolute;
    top: 0;
    bottom: 0;
    background: #00856f;
  }
  .avail-ticks {
    display: flex;
    justify-content: space-between;
    color: #59656f;
    font-size: 11px;
    margin-bottom: 9px;
  }
  table {
    border-collapse: collapse;
    width: 100%;
    font-size: 12px;
  }
  th, td {
    border: 1px solid #d7dcda;
    padding: 4px 5px;
    text-align: left;
    vertical-align: top;
  }
  th {
    background: #f0f2f1;
  }
  .leaflet-container {
    font: inherit;
  }
</style>
"""
def build_arg_parser(config_defaults: dict[str, object] | None = None) -> argparse.ArgumentParser:
    config_defaults = config_defaults or {}
    today = date.today().isoformat()
    parser = argparse.ArgumentParser(
        description="Query FDSN station availability and make an interactive station map.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--config",
        default="config.json",
        help="JSON config file. search_criteria values are used as defaults.",
    )
    parser.add_argument(
        "--bbox",
        default=config_defaults.get("bbox", "-23.6,-20.3,63.6,64.6"),
        help="Bounding box as min_lon,max_lon,min_lat,max_lat.",
    )
    parser.add_argument("--min-lon", type=float, default=None, help="Minimum longitude.")
    parser.add_argument("--max-lon", type=float, default=None, help="Maximum longitude.")
    parser.add_argument("--min-lat", type=float, default=None, help="Minimum latitude.")
    parser.add_argument("--max-lat", type=float, default=None, help="Maximum latitude.")
    parser.add_argument(
        "--start",
        default=config_defaults.get("start", "2020-01-01"),
        help="Start date, YYYY-MM-DD.",
    )
    parser.add_argument(
        "--end",
        default=config_defaults.get("end", today),
        help="End date, YYYY-MM-DD.",
    )
    parser.add_argument(
        "--clients",
        default=config_defaults.get("clients", "auto"),
        help=(
            "Comma-separated ObsPy FDSN clients or service URLs. "
            "Use 'auto' for routing plus common European/global data centers, "
            "or 'routing' for iris-federator and eida-routing only."
        ),
    )
    parser.add_argument(
        "--networks",
        default=config_defaults.get("networks", "*"),
        help="FDSN network selector.",
    )
    parser.add_argument(
        "--stations",
        default=config_defaults.get("stations", "*"),
        help="FDSN station selector.",
    )
    parser.add_argument(
        "--locations",
        default=config_defaults.get("locations", "*"),
        help="FDSN location selector.",
    )
    parser.add_argument(
        "--channels",
        default=config_defaults.get("channels", "*"),
        help="FDSN channel selector. '*' is broad and catches DAS channels such as HSF/MSF.",
    )
    parser.add_argument(
        "--das-networks",
        default=",".join(sorted(DEFAULT_DAS_NETWORKS)),
        help="Networks to classify as DAS.",
    )
    parser.add_argument(
        "--das-channels",
        default=",".join(sorted(DEFAULT_DAS_CHANNELS)),
        help="Channel codes to treat as DAS even when the network is not listed in --das-networks.",
    )
    parser.add_argument(
        "--collapse-das",
        action="store_true",
        help="Collapse all DAS nodes in each network into one marker.",
    )
    parser.add_argument(
        "--no-split-station-moves",
        action="store_true",
        help="Do not split same-code stations that have multiple coordinate positions.",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=config_defaults.get("timeout", 35),
        help="FDSN service timeout in seconds.",
    )
    parser.add_argument(
        "--include-restricted",
        action=argparse.BooleanOptionalAction,
        default=config_defaults.get("include_restricted", False),
        help="Include restricted station metadata in availability checks.",
    )
    parser.add_argument(
        "--obspy-double-check",
        action=argparse.BooleanOptionalAction,
        default=config_defaults.get("obspy_double_check", True),
        help="Run an ObsPy RoutingClient pass after direct curl station queries.",
    )
    parser.add_argument(
        "--obspy-routers",
        default=config_defaults.get("obspy_routers", ",".join(DEFAULT_OBSPY_ROUTERS)),
        help="Comma-separated ObsPy routing clients used for the double-check pass.",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=config_defaults.get("workers", 6),
        help="Parallel FDSN queries.",
    )
    parser.add_argument(
        "--output",
        default=config_defaults.get("output", DEFAULT_OUTPUT_HTML),
        help="Output HTML file.",
    )
    parser.add_argument(
        "--csv",
        default=config_defaults.get("csv", DEFAULT_OUTPUT_CSV),
        help="Fresh channel-window CSV output.",
    )
    parser.add_argument(
        "--basemap",
        choices=["nolabels", "osm", "dark"],
        default=config_defaults.get("basemap", "nolabels"),
        help="Background map style. 'nolabels' avoids place-name clutter.",
    )
    parser.add_argument(
        "--max-popup-rows",
        type=int,
        default=config_defaults.get("max_popup_rows", 90),
        help="Maximum channel rows shown in each station popup.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    config_parser = argparse.ArgumentParser(add_help=False)
    config_parser.add_argument("--config", default="config.json")
    config_args, _ = config_parser.parse_known_args(argv)
    try:
        config_defaults = load_config_defaults(config_args.config)
    except argparse.ArgumentTypeError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    parser = build_arg_parser(config_defaults)
    args = parser.parse_args(argv)
    start = parse_date(args.start, "start")
    end = parse_date(args.end, "end")
    if start > end:
        parser.error("--start must be before --end")
    try:
        bbox = resolve_bbox(args)
    except argparse.ArgumentTypeError as exc:
        parser.error(str(exc))
    das_networks = {item.strip() for item in args.das_networks.split(",") if item.strip()}
    das_channels = {item.strip().upper() for item in args.das_channels.split(",") if item.strip()}

    segments, service_status = query_services(args)
    if not segments:
        print("No station/channel availability found for this request.", file=sys.stderr)
        return 1

    csv_path = Path(args.csv).expanduser().resolve()
    html_path = Path(args.output).expanduser().resolve()
    groups = group_segments(
        segments,
        das_networks,
        das_channels,
        collapse_das=args.collapse_das,
        split_station_moves=not args.no_split_station_moves,
    )
    write_csv(segments, csv_path)
    write_map(groups, service_status, html_path, start, end, bbox, args.max_popup_rows, args.basemap)

    counts = defaultdict(int)
    for group in groups:
        counts[group.kind] += 1
    print(f"Wrote fresh segment CSV: {csv_path}")
    print(f"Wrote interactive map: {html_path}")
    print(
        f"Station groups: {len(groups)} "
        f"({counts['seismic']} seismic, {counts['DAS']} DAS, {counts['synthetic']} synthetic)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
