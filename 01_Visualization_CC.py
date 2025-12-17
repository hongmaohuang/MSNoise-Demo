# %%
import obspy
import matplotlib.pyplot as plt
import numpy as np
import os
import sys
from config_loader import load_config

plt.rcParams['font.family'] = 'Nimbus Sans'
plt.rcParams['font.size'] = 13

config = load_config()
viz_config = config.get("visualization", {})

cc_files_template = viz_config.get("cc_files_template") 
dtt_folder_template = viz_config.get("dtt_folder_template")

f_set = viz_config.get("filter_set")
comp = viz_config.get("component")  
ccf_date = viz_config.get("ccf_date")
figs_folder = viz_config.get("figs_folder")
raw_sta1 = viz_config.get("station1")
raw_sta2 = viz_config.get("station2")

base_path = cc_files_template.format(filter_set=f_set, component=comp)

sta1_str = raw_sta1.replace("-", "_")
sta2_str = raw_sta2.replace("-", "_")
pair_list = sorted([sta1_str, sta2_str])
station_pair_folder = f"{pair_list[0]}_{pair_list[1]}"

file_path = os.path.join(base_path, station_pair_folder, f"{ccf_date}.MSEED")

print(f"Plot for {comp}")
print(f"Reading the path: {file_path}")


if not os.path.exists(figs_folder):
    os.makedirs(figs_folder)

st = obspy.read(file_path)
print(f"{len(st)} Traces in this stream")

net1, name1 = raw_sta1.split("-")
net2, name2 = raw_sta2.split("-")

tr = st[0] 

npts = tr.stats.npts
sampling_rate = tr.stats.sampling_rate
maxlag = (npts - 1) / 2 / sampling_rate
time_axis = np.linspace(-maxlag, maxlag, npts)
data = tr.data

plt.figure(figsize=(10, 5))
plt.plot(time_axis, data, color='black', linewidth=0.8)

title_str = f"CCF: {raw_sta1} - {raw_sta2} ({comp})\nDate: {ccf_date} | Filter: {f_set}"
plt.title(title_str)
plt.xlabel("Lag Time (s)")
plt.ylabel("Amplitude")
plt.xlim(-maxlag, maxlag) 
plt.grid(True, which='both', linestyle='--', alpha=0.5)
plt.tight_layout()

out_name = f"CCF_{name1}_{name2}_{comp}_{f_set}_{ccf_date}.png"
save_path = os.path.join(figs_folder, out_name)

plt.savefig(save_path, dpi=300)

#
# DVV
#
import matplotlib.pyplot as plt
import pandas as pd
import numpy as np
import os
import glob
import sys
from config_loader import load_config

plt.rcParams['font.family'] = 'Nimbus Sans'
plt.rcParams['font.size'] = 13

config = load_config()
viz_config = config.get("visualization", {})

dtt_template = viz_config.get("dtt_folder_template")
f_set = viz_config.get("filter_set")
comp = viz_config.get("component")
figs_folder = viz_config.get("figs_folder")
dvv_mode = viz_config.get("dvv_target", "ALL")

dtt_folder = dtt_template.format(filter_set=f_set, component=comp)

if dvv_mode == "ALL":
    target_name = "ALL"
    print("Mode: (ALL)")
else:
    raw_s1 = viz_config.get("station1")
    raw_s2 = viz_config.get("station2")
    
    s1 = raw_s1.replace("-", "_")
    s2 = raw_s2.replace("-", "_")
    
    pair_list = sorted([s1, s2])
    target_name = f"{pair_list[0]}_{pair_list[1]}"
    print(f"Mode: Stations Pair ({target_name})")

txt_files = sorted(glob.glob(os.path.join(dtt_folder, "*.txt")))

if not txt_files:
    print(f"Error: There is no .txt file: {dtt_folder}")
    sys.exit(1)

print(f"{len(txt_files)} dtt files are found, processing...")

data_list = []

for f in txt_files:
    try:
        df_temp = pd.read_csv(f)
        
        row = df_temp[df_temp['Pairs'] == target_name]
        
        if not row.empty:
            val_m0 = row.iloc[0]['M0']
            val_em0 = row.iloc[0]['EM0']
            val_date = row.iloc[0]['Date']
            
            data_list.append({
                'day': val_date,
                'm0': val_m0,
                'error': val_em0
            })
            
    except Exception as e:
        print(f"Error {f}: {e}")
        continue

if not data_list:
    print(f"warning: there is no data for {target_name} in the DTT files.")
    sys.exit(1)

df = pd.DataFrame(data_list)
df['day'] = pd.to_datetime(df['day'])
df = df.sort_values('day')

dvv_percent = -df['m0'] * 100
err_percent = df['error'] * 100

if not os.path.exists(figs_folder):
    os.makedirs(figs_folder)

plt.figure(figsize=(10, 5))

plt.errorbar(df['day'], dvv_percent, yerr=err_percent, 
             fmt='o-', color='black', ecolor='gray', 
             capsize=3, markersize=4, linewidth=1, label=target_name)

title_str = f"Relative Velocity Change (dv/v)\nTarget: {target_name} | Filter: {f_set} ({comp})"
plt.title(title_str)
plt.xlabel("Date")
plt.ylabel("dv/v (%)")

plt.xlim(df['day'].min(), df['day'].max())
plt.xticks(rotation=30)
plt.grid(True, linestyle='--', alpha=0.5)
plt.tight_layout()

out_filename = f"dv_v_{target_name}_{f_set}_{comp}.png"
save_path = os.path.join(figs_folder, out_filename)
plt.savefig(save_path, dpi=300)
