# %%
# Under debugging!!!!!!
#  
import obspy
import os
import numpy as np
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from obspy import UTCDateTime

plt.rcParams['font.family'] = 'Nimbus Sans'
plt.rcParams['font.size'] = 13

PAIR_FOLDER = "STACKS/01/005_DAYS/ZZ/5S_R7B57_5S_R94DB" 
MAX_LAG_TIME = 60.0  
NORMALIZE = True     

def plot_ccf_heatmap():
    if not os.path.exists(PAIR_FOLDER):
        print(f"There is no: {PAIR_FOLDER}")
        return

    print(f"Reading: {PAIR_FOLDER} ...")
    
    file_list = sorted([f for f in os.listdir(PAIR_FOLDER) if f.endswith(".MSEED")])
    if not file_list:
        print("The folder is empty")
        return

    data_matrix = []
    dates = []
    lags = None

    for fname in file_list:
        path = os.path.join(PAIR_FOLDER, fname)
        try:
            st = obspy.read(path)
            tr = st[0]
            
            npts = tr.stats.npts
            samprate = tr.stats.sampling_rate
            t_axis = np.linspace(-((npts-1)/2)/samprate, ((npts-1)/2)/samprate, npts)
            
            mask = (t_axis >= -MAX_LAG_TIME) & (t_axis <= MAX_LAG_TIME)
            cut_data = tr.data[mask]
            
            if lags is None:
                lags = t_axis[mask]
            
            if NORMALIZE:
                cut_data = cut_data / np.max(np.abs(cut_data))
                
            data_matrix.append(cut_data)
            
            date_str = fname.replace(".MSEED", "")
            dates.append(UTCDateTime(date_str).datetime)
            
        except Exception as e:
            print(f"Skipping {fname}: {e}")

    matrix = np.array(data_matrix).T 

    fig, ax = plt.subplots(figsize=(10, 6))
    
    date_nums = mdates.date2num(dates)
    
    # extent = [x_min, x_max, y_min, y_max]
    im = ax.imshow(matrix, aspect='auto', cmap='seismic', 
                   extent=[date_nums[0], date_nums[-1], lags[0], lags[-1]],
                   vmin=-1, vmax=1, interpolation='nearest')

    ax.xaxis_date()
    ax.xaxis.set_major_formatter(mdates.DateFormatter('%Y-%m-%d'))
    fig.autofmt_xdate()

    plt.title(f"CCF Temporal Evolution: {os.path.basename(PAIR_FOLDER)}")
    plt.ylabel("Lapse Time (s)")
    plt.xlabel("Date")
    plt.colorbar(im, label="Normalized Amplitude")
    plt.grid(False)
    plt.tight_layout()
    plt.show()

if __name__ == "__main__":
    plot_ccf_heatmap()

# %%
import sqlite3
import pandas as pd
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import seaborn as sns 
import os

DB_PATH = "msnoise.sqlite"
TARGET_PAIR = "5S_R7B57_5S_R94DB" 


def plot_dvv_spectrum():
    if not os.path.exists(DB_PATH):
        print("Cannot find the database file")
        return

    conn = sqlite3.connect(DB_PATH)

    sql = f"""
    SELECT r.day, r.m as dvv, f.ref, f.low, f.high
    FROM results as r
    JOIN filters as f ON r.filterid = f.ref
    WHERE r.pair = '{TARGET_PAIR}' AND r.jobtype = 'DTT'
    ORDER BY f.low, r.day
    """
    
    try:
        df = pd.read_sql_query(sql, conn)
    except Exception as e:
        print(f"SQL Error: {e}")
        return
    conn.close()

    if df.empty:
        print(f"❌ 找不到配對 {TARGET_PAIR} 的資料，請確認名稱是否正確 (例如是用 : 還是 _ 分隔)")
        return

    df['freq_band'] = df.apply(lambda x: f"{x['low']}-{x['high']} Hz", axis=1)
    
    df['date'] = pd.to_datetime(df['day'])

    unique_filters = df['freq_band'].unique()
    print(f"🔍 發現 {len(unique_filters)} 個頻段: {unique_filters}")
    
    if len(unique_filters) < 2:
        print("⚠️ 警告：目前只有一個頻段，畫出來會像一條帶子，建議未來增加濾波器範圍。")

    pivot_df = df.pivot(index='freq_band', columns='date', values='dvv')
    
    pivot_df = pivot_df.sort_index(key=lambda x: [float(s.split('-')[0]) for s in x])

    plt.figure(figsize=(12, 6))
    
    ax = sns.heatmap(pivot_df, cmap='RdBu_r', center=0, 
                     cbar_kws={'label': 'dv/v (%)'},
                     xticklabels=5)

    date_labels = [d.strftime('%Y-%m-%d') for d in pivot_df.columns]
    ax.set_xticklabels(date_labels[::5], rotation=45) 
    
    plt.title(f"dv/v Interferogram (Frequency vs Time): {TARGET_PAIR}")
    plt.xlabel("Date")
    plt.ylabel("Frequency Band")
    plt.tight_layout()
    plt.show()

if __name__ == "__main__":
    try:
        import seaborn
        plot_dvv_spectrum()
    except ImportError:
        print("Please do this first: pip install seaborn")