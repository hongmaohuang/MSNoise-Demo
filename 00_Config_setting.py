import os
import glob
import csv
import sqlite3
import obspy
import subprocess
from obspy import UTCDateTime
from obspy.clients.fdsn import Client
from config_loader import load_config
from datetime import datetime

METADATA_CSV = "downloaded_stations_metadata.csv"

def step1_search_and_download(config):
    print("\n" + "="*60)
    print("STEP 1: Search & Download based on Config")
    print("="*60)

    search_cfg = config.get("search_criteria", {})
    proc_cfg = config.get("seismic_processing", {})
    
    start_time = UTCDateTime(search_cfg.get("start_date"))
    end_time   = UTCDateTime(search_cfg.get("end_date"))
    region     = search_cfg.get("region", {})
    clients    = search_cfg.get("clients", ["GFZ", "IRIS"])
    
    output_base_dir = proc_cfg.get("source_folder", "../Seismic_Data")
    
    min_lat, max_lat = region.get("min_lat"), region.get("max_lat")
    min_lon, max_lon = region.get("min_lon"), region.get("max_lon")

    print(f"Time Range: {start_time.date} to {end_time.date}")
    print(f"Region: Lat[{min_lat}, {max_lat}], Lon[{min_lon}, {max_lon}]")

    station_metadata = {}
    found_stations = []
    target_channels = "HH?,BH?,EH?" 

    print("\n--> Querying FDSN for available stations...")
    for client_name in clients:
        try:
            client = Client(client_name, timeout=30)
            inventory = client.get_stations(
                network="*", station="*", location="*", channel=target_channels,
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
    
    for sta_info in found_stations:
        net, sta = sta_info['net'], sta_info['sta']
        preferred_client = sta_info['client']
        
        station_dir = os.path.join(output_base_dir, sta)
        if not os.path.exists(station_dir): os.makedirs(station_dir)
            
        print(f"\nProcessing {net}.{sta} (Source: {preferred_client})")
        for i in range(total_days):
            t1 = start_time + (i * 86400)
            t2 = t1 + 86400
            date_str = t1.strftime("%Y-%m-%d")
            filename = os.path.join(station_dir, f"{net}.{sta}.{date_str}.mseed")
            
            if os.path.exists(filename):
                print(f"  - {date_str}: Exists (Skipping).") 
                continue
            try:
                client = Client(preferred_client, timeout=60)
                st = client.get_waveforms(net, sta, "*", target_channels, t1, t2)
                
                if len(st) > 0:
                    st.write(filename, format="MSEED")
                    comps = list(set([tr.stats.channel for tr in st]))
                    print(f"  - {date_str}: Downloaded {len(st)} traces. Chans: {comps}")
                else:
                    print(f"  - {date_str}: No data found on server.")
            except Exception as e:
                print(f"  - {date_str}: Failed ({str(e).splitlines()[0]})")

def step2_process_to_sds(config):
    print("\n" + "="*60)
    print("STEP 2: Processing Data to SDS Structure")
    print("="*60)
    
    proc_cfg = config.get("seismic_processing", {})
    source_folder = proc_cfg.get("source_folder")
    output_folder = proc_cfg.get("output_folder", "SDS")
    
    if not os.path.exists(output_folder): os.makedirs(output_folder)
    
    search_path = os.path.join(source_folder, "*")
    for station_dir in glob.glob(search_path):
        if not os.path.isdir(station_dir): continue
        
        print(f"--> Scanning raw folder: {os.path.basename(station_dir)}")
        for filepath in glob.glob(os.path.join(station_dir, "*.mseed")):
            try:
                st = obspy.read(filepath)
                try:
                    st.merge(method=1, fill_value='interpolate')
                except: pass
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
                pass
    print("SDS Structure Update Completed.")

def step3_scan_to_db(config):
    print("\n" + "="*60)
    print("STEP 3: Update DB & Scan SDS")
    print("="*60)

    scan_cfg = config.get("data_scan", {})
    search_cfg = config.get("search_criteria", {})
    
    sds_root = scan_cfg.get("sds_root")
    db_path = scan_cfg.get("db_path", "msnoise.sqlite")
    
    if not os.path.exists(db_path):
        print(f"Hey, there is no {db_path}！")
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
        cursor.execute("INSERT OR REPLACE INTO config (name, value) VALUES ('components_to_compute_single_station', 'ZZ,NN,EE,ZN,ZE,NE')")

        if os.path.exists(METADATA_CSV):
            print("--> Loading station coordinates from CSV...")
            cursor.execute("DELETE FROM stations")
            with open(METADATA_CSV, 'r', encoding='utf-8') as f:
                reader = csv.DictReader(f)
                for row in reader:
                    cursor.execute("""
                        INSERT INTO stations (net, sta, X, Y, altitude, coordinates, instrument, used)
                        VALUES (?, ?, ?, ?, ?, 'DEG', 'INST', 1)
                    """, (row['Network'], row['Station'], float(row['Longitude']), float(row['Latitude']), float(row['Elevation'])))
        else:
            print("Warning: No metadata CSV found.")

        raw_filters = scan_cfg.get("filter_config", [])
        
        if isinstance(raw_filters, dict):
            raw_filters = [raw_filters]
            
        print(f"--> Updating Filters (Found {len(raw_filters)} filters)...")
        
        cursor.execute("DELETE FROM filters")
        
        for fcfg in raw_filters:
            try:
                cursor.execute("""
                    INSERT INTO filters (ref, low, mwcs_low, high, mwcs_high, rms_threshold, mwcs_wlen, mwcs_step, used)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1)
                """, (
                    fcfg['ref'], 
                    fcfg['low'], 
                    fcfg['mwcs_low'], 
                    fcfg['high'], 
                    fcfg['mwcs_high'], 
                    fcfg['rms_threshold'], 
                    fcfg['mwcs_wlen'], 
                    fcfg['mwcs_step']
                ))
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
        for root, dirs, files in os.walk(sds_full_path):
            for file in files:
                try:
                    st = obspy.read(os.path.join(root, file), headonly=True)
                    tr = st[0]
                    rel_dir = os.path.relpath(root, sds_root)
                    
                    cursor.execute("""
                        INSERT INTO data_availability (net, sta, comp, path, file, starttime, endtime, data_duration, gaps_duration, samplerate, flag)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, ?, 'N')
                    """, (tr.stats.network, tr.stats.station, tr.stats.channel, rel_dir, file, 
                          tr.stats.starttime.datetime, tr.stats.endtime.datetime, 
                          tr.stats.endtime - tr.stats.starttime, tr.stats.sampling_rate))
                    count += 1
                except: pass
        
        print(f"Scan complete. {count} files registered in database.")
        conn.commit()

    except Exception as e:
        print(f"DB Error: {e}")
    finally:
        conn.close()

if __name__ == "__main__":
    try:
        conf = load_config()
        step1_search_and_download(conf)
        step2_process_to_sds(conf)
        step3_scan_to_db(conf)
        print("\nAll Done! Now you can run 'msnoise new_jobs --init' and 'msnoise compute_cc'.")
    except Exception as e:
        print(f"Execution Error: {e}")