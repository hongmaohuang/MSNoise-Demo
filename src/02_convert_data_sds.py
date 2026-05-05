#!/usr/bin/env python3

import argparse
import glob
import os

import obspy
from obspy import UTCDateTime

from config_loader import load_config, validate_config


def _ensure_dir(path: str) -> None:
    if not os.path.exists(path):
        os.makedirs(path)


def convert_to_sds(config):
    print("\n" + "=" * 60)
    print("STEP 2: Processing Data to SDS Structure")
    print("=" * 60)

    proc_cfg = config.get("seismic_processing", {})
    source_folder = proc_cfg.get("source_folder")
    output_folder = proc_cfg.get("output_folder", "SDS")

    _ensure_dir(output_folder)

    if not source_folder or not os.path.exists(source_folder):
        raise FileNotFoundError(f"source_folder does not exist: {source_folder}")

    search_path = os.path.join(source_folder, "*")
    for station_dir in glob.glob(search_path):
        if not os.path.isdir(station_dir):
            continue

        print(f"--> Scanning raw folder: {os.path.basename(station_dir)}")
        for filepath in glob.glob(os.path.join(station_dir, "*.mseed")):
            try:
                st = obspy.read(filepath)
                try:
                    st.merge(method=1, fill_value="interpolate")
                except Exception as exc:
                    print(f"    [WARN] Merge failed for {filepath}: {exc}")

                for tr in st:
                    original_chan = tr.stats.channel
                    print(
                        f"   [DEBUG] {tr.stats.network}.{tr.stats.station} "
                        f"original chanel: {original_chan}",
                        end=" => ",
                    )

                    if len(original_chan) >= 1:
                        last_char = original_chan[-1].upper()

                        if last_char in ["Z", "N", "E"]:
                            print(f"Keep the chanels ({original_chan})")
                        elif last_char == "1":
                            new_chan = original_chan[:-1] + "N"
                            tr.stats.channel = new_chan
                            print(f"Modify the chanel ({original_chan} -> {new_chan})")
                        elif last_char == "2":
                            new_chan = original_chan[:-1] + "E"
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
                            net, sta, chan = (
                                day_slice.stats.network,
                                day_slice.stats.station,
                                day_slice.stats.channel,
                            )

                            save_dir = os.path.join(output_folder, year, net, sta)
                            if not os.path.exists(save_dir):
                                os.makedirs(save_dir)

                            doy = current_time.julday
                            fname = f"{net}.{sta}..{chan}.D.{year}.{doy:03d}"

                            final_path = os.path.join(save_dir, fname)
                            if not os.path.exists(final_path):
                                day_slice.write(final_path, format="MSEED")
                        current_time = next_day
            except Exception as exc:
                print(f"    [WARN] Failed to process {filepath}: {exc}")
    print("SDS Structure Update Completed.")


if __name__ == "__main__":
    try:
        parser = argparse.ArgumentParser(description="Convert downloaded MiniSEED files to SDS.")
        parser.add_argument("--config", default="config.json", help="Workflow JSON config file.")
        args = parser.parse_args()
        conf = load_config(args.config)
        validate_config(conf)
        convert_to_sds(conf)
        print("\nAll Done! Run the scan script to update the MSNoise DB.")
    except Exception as exc:
        print(f"Execution Error: {exc}")
