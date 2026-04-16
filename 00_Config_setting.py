import os
import glob
import csv
import obspy
from collections import defaultdict
from obspy import UTCDateTime
from obspy.clients.fdsn import Client
from config_loader import load_config, validate_config
from typing import Iterable

METADATA_CSV = "downloaded_stations_metadata.csv"
FAILED_DOWNLOADS_CSV = "failed_waveform_downloads.csv"

def _ensure_dir(path: str) -> None:
    if not os.path.exists(path):
        os.makedirs(path)

def _iter_client_order(preferred: str, all_clients: Iterable[str]) -> Iterable[str]:
    ordered = [preferred] + [c for c in all_clients if c != preferred]
    return ordered

def _download_day(
    net: str,
    sta: str,
    station_dir: str,
    date_str: str,
    t1: UTCDateTime,
    t2: UTCDateTime,
    preferred_client: str,
    clients: Iterable[str],
    locations: str,
    channels: str,
    waveform_timeout: int,
) -> tuple[str, str | None, str | None]:
    filename = os.path.join(station_dir, f"{net}.{sta}.{date_str}.mseed")

    if os.path.exists(filename):
        print(f"  - {date_str}: Exists (Skipping).")
        return "exists", None, filename

    last_err = None
    for client_name in _iter_client_order(preferred_client, clients):
        try:
            client = Client(client_name, timeout=waveform_timeout)
            st = client.get_waveforms(net, sta, locations, channels, t1, t2)
            if len(st) > 0:
                st.write(filename, format="MSEED")
                comps = sorted(set(tr.stats.channel for tr in st))
                print(f"  - {date_str}: Downloaded {len(st)} traces from {client_name}. Chans: {comps}")
                return "downloaded", None, filename
            print(f"  - {date_str}: No data found on {client_name}.")
            return "no_data", None, filename
        except Exception as e:
            last_err = e

    msg = str(last_err).splitlines()[0] if last_err else "Unknown error"
    print(f"  - {date_str}: Skipped for retry ({msg})")
    return "skipped", msg, filename


def _write_failed_downloads(failures: list[dict[str, str]]) -> None:
    with open(FAILED_DOWNLOADS_CSV, "w", newline="", encoding="utf-8") as csvfile:
        writer = csv.DictWriter(
            csvfile,
            fieldnames=["network", "station", "date", "preferred_client", "filename", "error"],
        )
        writer.writeheader()
        writer.writerows(failures)


def _print_download_summary(summary: dict[str, dict[str, int]], final_failures: list[dict[str, str]]) -> None:
    print("\n--> Waveform download summary")
    for sta_key in sorted(summary):
        counts = summary[sta_key]
        parts = [
            f"downloaded={counts['downloaded']}",
            f"exists={counts['exists']}",
            f"no_data={counts['no_data']}",
            f"first_pass_skipped={counts['skipped']}",
            f"retried={counts['retried']}",
            f"failed={counts['failed']}",
        ]
        print(f"    {sta_key}: " + ", ".join(parts))

    if not final_failures:
        if os.path.exists(FAILED_DOWNLOADS_CSV):
            os.remove(FAILED_DOWNLOADS_CSV)
        print("    No skipped days remain after retry.")
        return

    _write_failed_downloads(final_failures)
    print(f"    Final skipped days: {len(final_failures)}")
    print(f"    Saved failed download list to {FAILED_DOWNLOADS_CSV}")

def step1_search_and_download(config):
    print("\n" + "="*60)
    print("STEP 1: Search & Download based on Config")
    print("="*60)

    search_cfg = config.get("search_criteria", {})
    proc_cfg = config.get("seismic_processing", {})
    
    start_time = UTCDateTime(search_cfg.get("start_date"))
    end_time = UTCDateTime(search_cfg.get("end_date"))
    region     = search_cfg.get("region", {})
    clients    = search_cfg.get("clients", ["GFZ", "IRIS"])
    target_networks = search_cfg.get("networks", "*")
    target_stations = search_cfg.get("stations", "*")
    target_locations = search_cfg.get("locations", "*")
    target_channels = search_cfg.get("channels", "HH?,BH?,EH?")
    waveform_timeout = int(search_cfg.get("waveform_timeout", 30))
    
    output_base_dir = proc_cfg.get("source_folder", "../Seismic_Data")
    
    min_lat, max_lat = region.get("min_lat"), region.get("max_lat")
    min_lon, max_lon = region.get("min_lon"), region.get("max_lon")

    if start_time >= end_time:
        raise ValueError(f"start_date must be before end_date: {start_time} >= {end_time}")

    print(f"Time Range: {start_time.date} to {end_time.date}")
    print(f"Region: Lat[{min_lat}, {max_lat}], Lon[{min_lon}, {max_lon}]")
    print(f"Networks: {target_networks}")
    print(f"Stations: {target_stations}")
    print(f"Locations: {target_locations}")
    print(f"Channels: {target_channels}")
    print(f"Waveform timeout: {waveform_timeout} seconds")

    station_metadata = {}
    found_stations = []

    print("\n--> Querying FDSN for available stations...")
    for client_name in clients:
        try:
            client = Client(client_name, timeout=30)
            inventory = client.get_stations(
                network=target_networks, station=target_stations, location=target_locations, channel=target_channels,
                starttime=start_time, endtime=end_time,
                minlatitude=min_lat, maxlatitude=max_lat,
                minlongitude=min_lon, maxlongitude=max_lon,
                level="channel" 
            )
            
            valid_count = 0
            for net in inventory:
                for sta in net:
                    if not (min_lat <= sta.latitude <= max_lat and min_lon <= sta.longitude <= max_lon):
                        continue
                    
                    avail_chans = set([c.code for c in sta.channels])
                    has_horizontal = any(c[-1] in ['N', 'E', '1', '2'] for c in avail_chans)
                    
                    msg_suffix = ""
                    if not has_horizontal:
                        msg_suffix = f" [WARN: Only has {avail_chans}, NO Horizontal!]"

                    sta_key = f"{net.code}.{sta.code}"
                    if sta_key not in station_metadata:
                        station_metadata[sta_key] = {
                            "net": net.code, "sta": sta.code,
                            "lat": sta.latitude, "lon": sta.longitude, "elev": sta.elevation
                        }
                        found_stations.append({
                            "net": net.code, "sta": sta.code, "client": client_name
                        })
                        valid_count += 1
                        
                        if not has_horizontal:
                            print(f"    - Found {sta_key} ({client_name}).{msg_suffix}")

            print(f"    [{client_name}] Valid stations found: {valid_count}")

        except Exception as e:
            print(f"    [{client_name}] Query info: {e}")

    if not station_metadata:
        print("No stations found in this region!")
        return

    with open(METADATA_CSV, "w", newline="", encoding="utf-8") as csvfile:
        writer = csv.writer(csvfile)
        writer.writerow(["Network", "Station", "Longitude", "Latitude", "Elevation"])
        for k, v in station_metadata.items():
            writer.writerow([v['net'], v['sta'], v['lon'], v['lat'], v['elev']])
    print(f"--> Metadata saved to {METADATA_CSV}")

    total_days = int((end_time - start_time) / 86400) + 1
    skipped_for_retry = []
    summary = defaultdict(lambda: defaultdict(int))
    
    for sta_info in found_stations:
        net, sta = sta_info['net'], sta_info['sta']
        preferred_client = sta_info['client']
        sta_key = f"{net}.{sta}"
        
        station_dir = os.path.join(output_base_dir, f"{net}.{sta}")
        _ensure_dir(station_dir)
            
        print(f"\nProcessing {net}.{sta} (Source: {preferred_client})")
        for i in range(total_days):
            t1 = start_time + (i * 86400)
            t2 = t1 + 86400
            date_str = t1.strftime("%Y-%m-%d")
            status, error, filename = _download_day(
                net,
                sta,
                station_dir,
                date_str,
                t1,
                t2,
                preferred_client,
                clients,
                target_locations,
                target_channels,
                waveform_timeout,
            )
            summary[sta_key][status] += 1
            if status == "skipped":
                skipped_for_retry.append(
                    {
                        "net": net,
                        "sta": sta,
                        "station_dir": station_dir,
                        "date_str": date_str,
                        "t1": t1,
                        "t2": t2,
                        "preferred_client": preferred_client,
                        "filename": filename or "",
                        "error": error or "",
                    }
                )

    final_failures = []
    if skipped_for_retry:
        print("\n--> Retrying skipped waveform days once...")
    for item in skipped_for_retry:
        net = item["net"]
        sta = item["sta"]
        sta_key = f"{net}.{sta}"
        print(f"\nRetrying {sta_key} {item['date_str']}")
        status, error, filename = _download_day(
            net,
            sta,
            item["station_dir"],
            item["date_str"],
            item["t1"],
            item["t2"],
            item["preferred_client"],
            clients,
            target_locations,
            target_channels,
            waveform_timeout,
        )
        summary[sta_key]["retried"] += 1
        if status == "downloaded":
            summary[sta_key]["downloaded"] += 1
        elif status == "exists":
            summary[sta_key]["exists"] += 1
        elif status == "no_data":
            summary[sta_key]["no_data"] += 1
        else:
            summary[sta_key]["failed"] += 1
            final_failures.append(
                {
                    "network": net,
                    "station": sta,
                    "date": item["date_str"],
                    "preferred_client": item["preferred_client"],
                    "filename": filename or item["filename"],
                    "error": error or item["error"],
                }
            )

    _print_download_summary(summary, final_failures)

def step2_process_to_sds(config):
    print("\n" + "="*60)
    print("STEP 2: Processing Data to SDS Structure")
    print("="*60)
    
    proc_cfg = config.get("seismic_processing", {})
    source_folder = proc_cfg.get("source_folder")
    output_folder = proc_cfg.get("output_folder", "SDS")
    
    _ensure_dir(output_folder)
    
    if not source_folder or not os.path.exists(source_folder):
        raise FileNotFoundError(f"source_folder does not exist: {source_folder}")

    search_path = os.path.join(source_folder, "*")
    for station_dir in glob.glob(search_path):
        if not os.path.isdir(station_dir): continue
        
        print(f"--> Scanning raw folder: {os.path.basename(station_dir)}")
        for filepath in glob.glob(os.path.join(station_dir, "*.mseed")):
            try:
                st = obspy.read(filepath)
                try:
                    st.merge(method=1, fill_value='interpolate')
                except Exception as e:
                    print(f"    [WARN] Merge failed for {filepath}: {e}")
                ''' 
                for tr in st:
                    original_chan = tr.stats.channel
                    if len(original_chan) >= 1:
                        last_char = original_chan[-1].upper()
                        if last_char in ['Z', 'N', 'E']:
                            pass 
                        else:
                            tr.stats.channel = "HHZ"
                    else:
                        tr.stats.channel = "HHZ"
                '''
                for tr in st:
                    original_chan = tr.stats.channel
                    
                    print(f"   [DEBUG] {tr.stats.network}.{tr.stats.station} original chanel: {original_chan}", end=" => ")

                    if len(original_chan) >= 1:
                        last_char = original_chan[-1].upper()
                        
                        if last_char in ['Z', 'N', 'E']:
                            print(f"Keep the chanels ({original_chan})") 
                            pass 
                        
                        elif last_char == '1':
                            new_chan = original_chan[:-1] + 'N'
                            tr.stats.channel = new_chan
                            print(f"Modify the chanel ({original_chan} -> {new_chan})") 

                        elif last_char == '2':
                            new_chan = original_chan[:-1] + 'E'
                            tr.stats.channel = new_chan
                            print(f"Modify the chanel ({original_chan} -> {new_chan})") 

                        else:
                            tr.stats.channel = "HHZ"
                            print(f"Force the component of DAS to Z (bc it's {last_char})") 
                            
                    else:
                        tr.stats.channel = "HHZ"
                        print("Force the component of DAS to Z (because no channel name)")
                    start_time = tr.stats.starttime
                    end_time = tr.stats.endtime
                    current_time = UTCDateTime(start_time.date)
                    
                    while current_time < end_time:
                        next_day = current_time + 86400
                        slice_start = current_time
                        slice_end = next_day - 0.000001
                        
                        day_slice = tr.slice(starttime=slice_start, endtime=slice_end)
                        if day_slice.stats.npts > 0:
                            year = str(current_time.year)
                            net, sta, chan = day_slice.stats.network, day_slice.stats.station, day_slice.stats.channel
                            
                            save_dir = os.path.join(output_folder, year, net, sta)
                            if not os.path.exists(save_dir): os.makedirs(save_dir)
                            
                            doy = current_time.julday
                            fname = f"{net}.{sta}..{chan}.D.{year}.{doy:03d}"
                            
                            final_path = os.path.join(save_dir, fname)
                            if not os.path.exists(final_path):
                                day_slice.write(final_path, format="MSEED")
                        current_time = next_day
            except Exception as e:
                print(f"    [WARN] Failed to process {filepath}: {e}")
    print("SDS Structure Update Completed.")

if __name__ == "__main__":
    try:
        conf = load_config()
        validate_config(conf)
        step1_search_and_download(conf)
        step2_process_to_sds(conf)
        print("\nAll Done! Run the scan script to update the MSNoise DB.")
    except Exception as e:
        print(f"Execution Error: {e}")
