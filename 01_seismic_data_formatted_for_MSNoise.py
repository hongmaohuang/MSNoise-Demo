import obspy
import os
from obspy import UTCDateTime

SOURCE_FOLDER = "../Seismic_Data"
OUTPUT_FOLDER = "SDS"

def process_seismic_data():
    if not os.path.exists(OUTPUT_FOLDER):
        os.makedirs(OUTPUT_FOLDER)

    print(f"Start processing data from {SOURCE_FOLDER} to {OUTPUT_FOLDER}...")

    for filename in os.listdir(SOURCE_FOLDER):
        if not filename.endswith(".mseed"):
            continue

        filepath = os.path.join(SOURCE_FOLDER, filename)
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


                        save_dir = os.path.join(OUTPUT_FOLDER, year_str, net, sta)
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