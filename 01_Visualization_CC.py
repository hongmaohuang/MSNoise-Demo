import obspy
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import os
import glob

from config_loader import load_config

plt.rcParams['font.family'] = 'Nimbus Sans'
plt.rcParams['font.size'] = 13

config = load_config()
viz_config = config.get("visualization", {})

cc_files = viz_config.get("cc_files")
dtt_folder = viz_config.get("dtt_folder")
network_1 = viz_config.get("station1").split("-")[0]
network_2 = viz_config.get("station2").split("-")[0]
station1 = viz_config.get("station1").split("-")[1]
station2 = viz_config.get("station2").split("-")[1]
figs_folder = viz_config.get("figs_folder")
ccf_date = viz_config.get("ccf_date")
dvv_target_pair = "ALL" 

if not all([cc_files, dtt_folder, station1, station2]):
    raise ValueError("Visualization config must define figs_folder, cc_files, dtt_folder, station1, station2, and ccf_date.")
if not os.path.exists(figs_folder):
    os.makedirs(figs_folder)

#
# CCF
#
filedir = f"{cc_files}/{network_1}_{station1}_{network_2}_{station2}/{ccf_date}.MSEED"
st = obspy.read(filedir)
tr = st[0]
npts = tr.stats.npts
sampling_rate = tr.stats.sampling_rate
maxlag = (npts - 1) / 2 / sampling_rate

time_axis = np.linspace(-maxlag, maxlag, npts)

data = tr.data

plt.figure(figsize=(10, 5))
plt.plot(time_axis, data, color='black', linewidth=0.8)

plt.title(f"CCF: {station1} - {station2}")
plt.xlabel("Lag Time (s)")
plt.ylabel("Amplitude")
plt.xlim(-maxlag, maxlag) 
plt.grid(True, which='both', linestyle='--', alpha=0.5)
plt.tight_layout()
plt.savefig(f"{figs_folder}/CCF_{network_1}_{station1}_{network_2}_{station2}.png", dpi=300)

#
# DVV
#
txt_files = sorted(glob.glob(os.path.join(dtt_folder, "*.txt")))
results = []
for f in txt_files:
    with open(f, 'r') as file:
        for line in file:
            if line.startswith("Date"):
                continue
            parts = line.strip().split(',')
            
            if len(parts) < 8: 
                continue
            row_date = parts[0]
            current_pair = parts[1]
            
            if current_pair == dvv_target_pair:
                try:
                    m0 = float(parts[6])
                    em0 = float(parts[7])
                    
                    results.append({
                        'day': row_date, 
                        'm0': m0, 
                        'error': em0
                    })
                except ValueError:
                    print(f"Value conversion error: {line}")

df = pd.DataFrame(results)
df['day'] = pd.to_datetime(df['day'])
df = df.sort_values('day')

dvv_percent = -df['m0'] * 100
err_percent = df['error'] * 100

plt.figure(figsize=(10, 5))
plt.errorbar(df['day'], dvv_percent, yerr=err_percent, 
                fmt='o-', color='black', ecolor='gray', 
                capsize=3, markersize=4, linewidth=1)

plt.title(f"Relative Velocity Change (dv/v): {dvv_target_pair}")
plt.xlabel("Date")
plt.ylabel("dv/v (%)")
plt.xlim(df.day.min(), df.day.max())
plt.xticks(rotation=30)
plt.grid(True, linestyle='--', alpha=0.5)
plt.tight_layout()
plt.savefig(f"./Figs/dv_v_{dvv_target_pair}.png", dpi=300)
