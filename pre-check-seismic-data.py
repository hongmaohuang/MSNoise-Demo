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
import math
import sys
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
    from obspy.clients.fdsn import Client, RoutingClient
except ImportError as exc:  # pragma: no cover - this is a user-facing guard.
    missing = exc.name or "required package"
    print(
        f"Missing Python package: {missing}\n\n"
        "Use an environment with ObsPy and Folium, for example:\n"
        "  conda run -n msnoise-hm python pre-check-seismic-data.py\n\n"
        "Or create one:\n"
        "  conda create -n seismic-precheck -c conda-forge python=3.11 obspy folium pandas\n"
        "  conda run -n seismic-precheck python pre-check-seismic-data.py",
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

DEFAULT_DAS_NETWORKS = {"1D", "5J", "ZH"}
DEFAULT_DAS_CHANNELS = {"HSF", "MSF"}
DEFAULT_OUTPUT_HTML = "pre_check_seismic_data.html"
DEFAULT_OUTPUT_CSV = "pre_check_seismic_segments.csv"


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


def service_client(name: str, timeout: int):
    lower = name.lower()
    if lower in {"iris-federator", "eida-routing"}:
        return RoutingClient(lower, timeout=timeout)
    if name.startswith("http://") or name.startswith("https://"):
        return Client(name, timeout=timeout)
    return Client(name, timeout=timeout)


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
) -> tuple[str, list[Segment], str | None]:
    min_lon, max_lon, min_lat, max_lat = bbox
    try:
        client = service_client(service_name, timeout=timeout)
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
        )
    except Exception as exc:  # FDSN servers often differ; keep scanning others.
        return service_name, [], str(exc).splitlines()[0]

    rows: list[Segment] = []
    for net in inventory:
        for sta in net:
            station_lat = float(sta.latitude)
            station_lon = float(sta.longitude)
            if not (min_lat <= station_lat <= max_lat and min_lon <= station_lon <= max_lon):
                continue
            for chan in sta.channels:
                chan_lat = float(getattr(chan, "latitude", station_lat) or station_lat)
                chan_lon = float(getattr(chan, "longitude", station_lon) or station_lon)
                if not (min_lat <= chan_lat <= max_lat and min_lon <= chan_lon <= max_lon):
                    continue

                seg_start = max(to_datetime(getattr(chan, "start_date", None), start), start)
                seg_end = min(to_datetime(getattr(chan, "end_date", None), end), end)
                if seg_start > end or seg_end < start or seg_start > seg_end:
                    continue

                rows.append(
                    Segment(
                        network=net.code,
                        station=sta.code,
                        location=getattr(chan, "location_code", "") or "--",
                        channel=chan.code,
                        lat=chan_lat,
                        lon=chan_lon,
                        elev_m=getattr(chan, "elevation", None),
                        sample_rate_hz=getattr(chan, "sample_rate", None),
                        start=seg_start,
                        end=seg_end,
                        sources={service_name},
                        sensor=get_sensor_text(chan),
                    )
                )
    return service_name, rows, None


def query_services(args: argparse.Namespace) -> tuple[list[Segment], list[tuple[str, str | None, int]]]:
    services = parse_clients(args.clients)
    start = parse_date(args.start, "start")
    end = parse_date(args.end, "end")
    bbox = resolve_bbox(args)

    print(f"Time: {args.start} to {args.end}")
    print(f"BBox: lon {bbox[0]} to {bbox[1]}, lat {bbox[2]} to {bbox[3]}")
    print(f"Channels: {args.channels}")
    print(f"Services: {', '.join(services)}")

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
                key = row.key
                if key in merged:
                    merged[key].sources.update(row.sources)
                    if not merged[key].sensor and row.sensor:
                        merged[key].sensor = row.sensor
                else:
                    merged[key] = row

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
    clients = [item.strip() for item in raw.split(",") if item.strip()]
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
def build_arg_parser() -> argparse.ArgumentParser:
    today = date.today().isoformat()
    parser = argparse.ArgumentParser(
        description="Query FDSN station availability and make an interactive station map.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--bbox",
        default="-23.6,-20.3,63.6,64.6",
        help="Bounding box as min_lon,max_lon,min_lat,max_lat.",
    )
    parser.add_argument("--min-lon", type=float, default=None, help="Minimum longitude.")
    parser.add_argument("--max-lon", type=float, default=None, help="Maximum longitude.")
    parser.add_argument("--min-lat", type=float, default=None, help="Minimum latitude.")
    parser.add_argument("--max-lat", type=float, default=None, help="Maximum latitude.")
    parser.add_argument("--start", default="2020-01-01", help="Start date, YYYY-MM-DD.")
    parser.add_argument("--end", default=today, help="End date, YYYY-MM-DD.")
    parser.add_argument(
        "--clients",
        default="auto",
        help=(
            "Comma-separated ObsPy FDSN clients or service URLs. "
            "Use 'auto' for routing plus common European/global data centers, "
            "or 'routing' for iris-federator and eida-routing only."
        ),
    )
    parser.add_argument("--networks", default="*", help="FDSN network selector.")
    parser.add_argument("--stations", default="*", help="FDSN station selector.")
    parser.add_argument("--locations", default="*", help="FDSN location selector.")
    parser.add_argument(
        "--channels",
        default="*",
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
    parser.add_argument("--timeout", type=int, default=35, help="FDSN service timeout in seconds.")
    parser.add_argument("--workers", type=int, default=6, help="Parallel FDSN queries.")
    parser.add_argument("--output", default=DEFAULT_OUTPUT_HTML, help="Output HTML file.")
    parser.add_argument("--csv", default=DEFAULT_OUTPUT_CSV, help="Fresh channel-window CSV output.")
    parser.add_argument(
        "--basemap",
        choices=["nolabels", "osm", "dark"],
        default="nolabels",
        help="Background map style. 'nolabels' avoids place-name clutter.",
    )
    parser.add_argument(
        "--max-popup-rows",
        type=int,
        default=90,
        help="Maximum channel rows shown in each station popup.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_arg_parser()
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
