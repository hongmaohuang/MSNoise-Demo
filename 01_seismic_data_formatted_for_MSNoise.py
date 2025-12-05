import obspy
import os
from obspy import UTCDateTime

from config_loader import load_config

def process_seismic_data():
    config = load_config()
    processing_config = config.get("seismic_processing", {})

    source_folder = processing_config.get("source_folder")
    output_folder = processing_config.get("output_folder")

    if not source_folder or not output_folder:
        raise ValueError("'source_folder' and 'output_folder' must be set in the seismic_processing config section.")

    if not os.path.exists(output_folder):
        os.makedirs(output_folder)

    print(f"Start processing data from {source_folder} to {output_folder}...")

    for filename in os.listdir(source_folder):
        if not filename.endswith(".mseed"):
            continue

        filepath = os.path.join(source_folder, filename)
        print(f"Processing {filename} ...")

        try:
            st = obspy.read(filepath)

            try:
                st.merge(method=1, fill_value='interpolate')
            except Exception as e:
                print(f"  Merge warning: {e}")

            for tr in st:
                original_channel = tr.stats.channel
                component = original_channel[-1].upper()
                if component not in ['N', 'E', 'Z']:
                     tr.stats.channel = "HHZ"
                     print(f"Channel {original_channel} changed to HHZ")

                start_time = tr.stats.starttime
                end_time = tr.stats.endtime


                current_time = UTCDateTime(start_time.date)

                while current_time < end_time:
                    next_day = current_time + 86400  
                    

                    slice_start = current_time
                    slice_end = next_day - 0.000001 


                    day_slice = tr.slice(starttime=slice_start, endtime=slice_end)


                    if day_slice.stats.npts > 0:
                        year_str = str(current_time.year)
                        net = day_slice.stats.network
                        sta = day_slice.stats.station
                        chan = day_slice.stats.channel
                        loc = day_slice.stats.location if day_slice.stats.location else ""


                        save_dir = os.path.join(output_folder, year_str, net, sta)
                        if not os.path.exists(save_dir):
                            os.makedirs(save_dir)

                        doy = current_time.julday
                        save_filename = f"{net}.{sta}.{loc}.{chan}.D.{year_str}.{doy:03d}"
                        save_path = os.path.join(save_dir, save_filename)

                        day_slice.write(save_path, format="MSEED")
                        print(f"  Saved: {save_filename} | pts: {day_slice.stats.npts}")

                    current_time = next_day

        except Exception as e:
            print(f"Error processing {filename}: {e}")

    print("Data processing completed.")

if __name__ == "__main__":
    process_seismic_data()