import obspy
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import os
import glob
plt.rcParams['font.family'] = 'Nimbus Sans'
plt.rcParams['font.size'] = 13

cc_files = "./STACKS/01/REF/ZZ"
dtt_folder = "./DTT/01/005_DAYS/ZZ"
network = "5J"
station1 = "02050"
station2 = "02818"
overplot_well = "SV-12_PT"
well_data = f"/media/kmaterna/rocket/hmhuang/Research/Iceland/Well_Data_confidential/{overplot_well}_confidential_data.csv"
#dvv_target_pair = f"5J_{station1}_5J_{station2}" 
dvv_target_pair = "ALL"
figs_folder = "./Figs"
if not os.path.exists(figs_folder):
    os.makedirs(figs_folder)

#
# CCF
#
filedir = f"{cc_files}/{network}_{station1}_{network}_{station2}.MSEED"
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
plt.legend()
plt.grid(True, which='both', linestyle='--', alpha=0.5)
plt.tight_layout()
plt.show()

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


well_ts = pd.read_csv(well_data)
well_ts['datetime'] = pd.to_datetime(well_ts['datetime'])



if dvv_target_pair == "ALL":
    fig, ax1 = plt.subplots(figsize=(10, 6))

    ax1.plot(well_ts.datetime, well_ts.P, linewidth=3, color="#5B8D54", alpha=0.8, label='Pressure')
    ax1.set_ylabel("Pressure (bar)", color="#5B8D54") 
    ax1.tick_params(axis='y', labelcolor="#5B8D54")
    ax1.grid(True, linestyle='--', alpha=0.5)
    ax1.set_ylim(20, 40)

    ax2 = ax1.twinx()  
    ax2.errorbar(df['day'], dvv_percent, yerr=err_percent, 
                fmt='o-', color="#6E84A7", ecolor='gray', 
                capsize=3, markersize=4, linewidth=1, label='dv/v')
    ax2.set_ylabel("dv/v (%)", color="#6E84A7")
    ax2.tick_params(axis='y', labelcolor="#6E84A7")

    ax2.axhline(0, color='black', linestyle='--', linewidth=0.8)
    plt.title(f"Relative Velocity Change (dv/v): {dvv_target_pair}")
    plt.xlim(df.day.min(), df.day.max())
    ax1.tick_params("x", rotation=30)

    plt.tight_layout()
    plt.savefig("./Figs/dv_v_with_well_data.png", dpi=300)
    
else:
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

# %%
