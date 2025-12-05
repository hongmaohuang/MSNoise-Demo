import os
import sqlite3
import obspy
from datetime import datetime

# ================= SETTINGS =================
# Absolute path to your SDS
sds_root = "/media/kmaterna/rocket/hmhuang/Research/Iceland/MSNoise_Demo"
db_path = "msnoise.sqlite"

# Station information (latest coordinates)
stations_info = [
    ("5J", "01412", -22.43064, 63.87888, 11.61),
    ("5J", "02050", -22.47506, 63.86989, 18.78),
    ("5J", "02818", -22.53514, 63.86642, 40.62)
]

# Filter configuration (0.1 - 1.0 Hz)
filter_config = {
    "ref": 1, "low": 0.1, "high": 1.0, "mwcs_low": 0.1, "mwcs_high": 1.0,
    "rms_threshold": 0.0, "mwcs_wlen": 12.0, "mwcs_step": 4.0
}
# ============================================

if not os.path.exists(db_path):
    print("ERROR: msnoise.sqlite not found. Please run 'msnoise db init' first!")
    exit()

conn = sqlite3.connect(db_path)
cursor = conn.cursor()

try:
    # --- 1. Fix schema if needed ---
    print("\n--- [1/4] Checking and repairing database schema ---")
    cursor.execute("PRAGMA table_info(stations)")
    cols = [c[1] for c in cursor.fetchall()]
    if 'startdate' not in cols:
        print(" -> Adding startdate column...")
        cursor.execute("ALTER TABLE stations ADD COLUMN startdate DATE")
    if 'enddate' not in cols:
        print(" -> Adding enddate column...")
        cursor.execute("ALTER TABLE stations ADD COLUMN enddate DATE")

    # --- 2. Write configuration ---
    print("\n--- [2/4] Writing global configuration ---")
    # Note: Your files are HSF, so you must compute 'FF' components; using ZZ may fail.
    configs = {
        'data_folder': sds_root,
        'data_structure': 'SDS',
        'data_type': 'D',
        'startdate': '2020-01-01',
        'enddate': '2021-01-01',
        'components_to_compute': 'ZZ',
        'channels': 'Z',
        'plugins': 'db_init'
    }
    for name, value in configs.items():
        cursor.execute("INSERT OR REPLACE INTO config (name, value) VALUES (?, ?)", (name, value))
    print("Config written (FF components, year 2020).")

    # --- 3. Write filters and stations ---
    print("\n--- [3/4] Writing filters and station information ---")

    # Filters
    cursor.execute("DELETE FROM filters")
    cursor.execute("""
        INSERT INTO filters (ref, low, mwcs_low, high, mwcs_high, rms_threshold,
                             mwcs_wlen, mwcs_step, used)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1)
    """, (filter_config['ref'], filter_config['low'], filter_config['mwcs_low'],
          filter_config['high'], filter_config['mwcs_high'], filter_config['rms_threshold'],
          filter_config['mwcs_wlen'], filter_config['mwcs_step']))

    # Stations
    cursor.execute("DELETE FROM stations")
    for net, sta, lon, lat, elev in stations_info:
        cursor.execute("""
            INSERT INTO stations
            (net, sta, X, Y, altitude, coordinates, instrument,
             startdate, enddate, used)
            VALUES (?, ?, ?, ?, ?, 'DEG', 'DAS', '2020-01-01', '2030-01-01', 1)
        """, (net, sta, lon, lat, elev))
    print(f"{len(stations_info)} stations inserted.")

    # --- 4. Manual scan of SDS directory ---
    print("\n--- [4/4] Scanning SDS files (ObsPy direct read) ---")
    cursor.execute("DELETE FROM data_availability")
    count = 0

    for root, dirs, files in os.walk(sds_root+'/SDS/'):
        for file in files:
            if file.startswith("."):
                continue  
            file_path = os.path.join(root, file)
            try:
                st = obspy.read(file_path, headonly=True)
                tr = st[0]

                net, sta = tr.stats.network, tr.stats.station
                comp = tr.stats.channel
                starttime = tr.stats.starttime.datetime
                endtime = tr.stats.endtime.datetime
                duration = tr.stats.endtime - tr.stats.starttime
                samplerate = tr.stats.sampling_rate

                rel_dir = os.path.relpath(root, sds_root)

                sql = """
                    INSERT INTO data_availability
                    (net, sta, comp, path, file, starttime, endtime,
                     data_duration, gaps_duration, samplerate, flag)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, ?, 'N')
                """
                cursor.execute(sql, (net, sta, comp, rel_dir, file,
                                     starttime, endtime, duration, samplerate))

                count += 1
                if count % 50 == 0:
                    print(f" -> Imported {count} files...")
            except:
                pass

    print(f"Scan complete! Total files imported: {count}")

    conn.commit()

except Exception as e:
    print(f"Error occurred: {e}")

finally:
    conn.close()
