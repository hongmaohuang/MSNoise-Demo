import csv
import os
import sqlite3
from datetime import datetime

import obspy

from config_loader import load_config, validate_config

METADATA_CSV = "downloaded_stations_metadata.csv"


def scan_to_db(config):
    print("\n" + "=" * 60)
    print("SCAN: Update DB & Scan SDS")
    print("=" * 60)

    scan_cfg = config.get("data_scan", {})
    search_cfg = config.get("search_criteria", {})

    sds_root = scan_cfg.get("sds_root")
    db_path = scan_cfg.get("db_path", "msnoise.sqlite")

    if not sds_root or sds_root == "your MSNoise working directory":
        raise ValueError("data_scan.sds_root must be set to your MSNoise working directory")

    if not os.path.exists(db_path):
        print(f"Hey, there is no {db_path}!")
        print("Please run this before this script: msnoise db init")
        return

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()

    try:
        start_date = search_cfg.get("start_date", "1970-01-01")
        end_date = search_cfg.get("end_date", "2099-01-01")
        today_str = datetime.now().strftime("%Y-%m-%d")

        cursor.execute("INSERT OR REPLACE INTO config (name, value) VALUES ('components_to_compute', 'ZZ,NN,EE')")
        cursor.execute("INSERT OR REPLACE INTO config (name, value) VALUES ('data_folder', ?)", (sds_root,))
        cursor.execute("INSERT OR REPLACE INTO config (name, value) VALUES ('data_structure', 'SDS')")
        cursor.execute("INSERT OR REPLACE INTO config (name, value) VALUES ('data_type', 'D')")
        cursor.execute("INSERT OR REPLACE INTO config (name, value) VALUES ('startdate', ?)", (start_date,))
        cursor.execute("INSERT OR REPLACE INTO config (name, value) VALUES ('enddate', ?)", (end_date,))
        cursor.execute("INSERT OR REPLACE INTO config (name, value) VALUES ('ref_end', ?)", (today_str,))
        cursor.execute(
            "INSERT OR REPLACE INTO config (name, value) VALUES ('components_to_compute_single_station', 'ZZ,NN,EE,ZN,ZE,NE')"
        )
        cursor.execute("INSERT OR REPLACE INTO config (name, value) VALUES ('dtt_lag', 'dynamic')")
        cursor.execute("INSERT OR REPLACE INTO config (name, value) VALUES ('dtt_v', '1.5')")
        cursor.execute("INSERT OR REPLACE INTO config (name, value) VALUES ('dtt_width', '30.0')")
        cursor.execute("INSERT OR REPLACE INTO config (name, value) VALUES ('dtt_sides', 'both')")
        cursor.execute("INSERT OR REPLACE INTO config (name, value) VALUES ('dtt_minlag', '5.0')")
        cursor.execute("INSERT OR REPLACE INTO config (name, value) VALUES ('stack_method', 'pws')")

        if os.path.exists(METADATA_CSV):
            print("--> Loading station coordinates from CSV...")
            cursor.execute("DELETE FROM stations")
            with open(METADATA_CSV, "r", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    cursor.execute(
                        """
                        INSERT INTO stations (net, sta, X, Y, altitude, coordinates, instrument, used)
                        VALUES (?, ?, ?, ?, ?, 'DEG', 'INST', 1)
                    """,
                        (
                            row["Network"],
                            row["Station"],
                            float(row["Longitude"]),
                            float(row["Latitude"]),
                            float(row["Elevation"]),
                        ),
                    )
        else:
            print("Warning: No metadata CSV found.")

        raw_filters = scan_cfg.get("filter_config", [])
        if isinstance(raw_filters, dict):
            raw_filters = [raw_filters]

        print(f"--> Updating Filters (Found {len(raw_filters)} filters)...")
        cursor.execute("DELETE FROM filters")

        for fcfg in raw_filters:
            try:
                cursor.execute(
                    """
                    INSERT INTO filters (ref, low, mwcs_low, high, mwcs_high, rms_threshold, mwcs_wlen, mwcs_step, used)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1)
                """,
                    (
                        fcfg["ref"],
                        fcfg["low"],
                        fcfg["mwcs_low"],
                        fcfg["high"],
                        fcfg["mwcs_high"],
                        fcfg["rms_threshold"],
                        fcfg["mwcs_wlen"],
                        fcfg["mwcs_step"],
                    ),
                )
                print(f"    - Added Filter ID {fcfg['ref']}: {fcfg['low']}-{fcfg['high']} Hz")
            except Exception as e_filt:
                print(f"    ! Error adding filter {fcfg.get('ref', '?')}: {e_filt}")

        print("--> Scanning SDS files to update database...")
        cursor.execute("DELETE FROM data_availability")

        sds_folder_name = config.get("seismic_processing", {}).get("output_folder", "SDS")
        sds_full_path = os.path.abspath(sds_folder_name)
        if not os.path.exists(sds_full_path):
            sds_full_path = os.path.join(sds_root, "SDS")

        count = 0
        for root, _, files in os.walk(sds_full_path):
            for file in files:
                try:
                    st = obspy.read(os.path.join(root, file), headonly=True)
                    tr = st[0]
                    rel_dir = os.path.relpath(root, sds_root)
                    cursor.execute(
                        """
                        INSERT INTO data_availability (net, sta, comp, path, file, starttime, endtime, data_duration, gaps_duration, samplerate, flag)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, ?, 'N')
                    """,
                        (
                            tr.stats.network,
                            tr.stats.station,
                            tr.stats.channel,
                            rel_dir,
                            file,
                            tr.stats.starttime.datetime,
                            tr.stats.endtime.datetime,
                            tr.stats.endtime - tr.stats.starttime,
                            tr.stats.sampling_rate,
                        ),
                    )
                    count += 1
                except Exception as e:
                    print(f"    [WARN] Failed reading SDS file {file}: {e}")

        print(f"Scan complete. {count} files registered in database.")
        conn.commit()

    except Exception as e:
        print(f"DB Error: {e}")
    finally:
        conn.close()


if __name__ == "__main__":
    try:
        conf = load_config()
        validate_config(conf)
        scan_to_db(conf)
        print("\nAll Done! You can now run 'msnoise new_jobs --init' and 'msnoise compute_cc'.")
    except Exception as e:
        print(f"Execution Error: {e}")
