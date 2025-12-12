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

PAIR_FOLDER = "STACKS/01/005_DAYS/ZZ/5J_02050_5J_02818" 
MAX_LAG_TIME = 60.0  # 畫圖範圍：只畫 -60秒 到 60秒
NORMALIZE = True     # 是否將每天的波形振幅歸一化 (建議 True，不然地震天會整條爆掉)
# =========================================

def plot_ccf_heatmap():
    if not os.path.exists(PAIR_FOLDER):
        print(f"❌ 找不到資料夾: {PAIR_FOLDER}")
        print("請修改 PAIR_FOLDER 變數，指向正確的配對資料夾。")
        return

    print(f"正在讀取波形檔: {PAIR_FOLDER} ...")
    
    file_list = sorted([f for f in os.listdir(PAIR_FOLDER) if f.endswith(".MSEED")])
    if not file_list:
        print("❌ 資料夾是空的！")
        return

    data_matrix = []
    dates = []
    lags = None

    for fname in file_list:
        path = os.path.join(PAIR_FOLDER, fname)
        try:
            st = obspy.read(path)
            tr = st[0]
            
            # 計算滯後時間軸 (Lapse Time Axis)
            npts = tr.stats.npts
            samprate = tr.stats.sampling_rate
            # 建立時間軸陣列 (-maxlag 到 +maxlag)
            t_axis = np.linspace(-((npts-1)/2)/samprate, ((npts-1)/2)/samprate, npts)
            
            # 擷取我們要的範圍 (-60 ~ 60)
            mask = (t_axis >= -MAX_LAG_TIME) & (t_axis <= MAX_LAG_TIME)
            cut_data = tr.data[mask]
            
            if lags is None:
                lags = t_axis[mask]
            
            # 歸一化 (讓每一天的能量看起來一致)
            if NORMALIZE:
                cut_data = cut_data / np.max(np.abs(cut_data))
                
            data_matrix.append(cut_data)
            
            # 檔名轉日期 (2020-07-01.MSEED -> datetime object)
            date_str = fname.replace(".MSEED", "")
            dates.append(UTCDateTime(date_str).datetime)
            
        except Exception as e:
            print(f"Skipping {fname}: {e}")

    # 轉成矩陣並轉置 (因為 imshow 需要 y, x)
    matrix = np.array(data_matrix).T 

    # --- 畫圖 ---
    fig, ax = plt.subplots(figsize=(10, 6))
    
    # 轉換日期為數字格式以便 imshow 使用
    date_nums = mdates.date2num(dates)
    
    # 使用 imshow 畫熱圖
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
    plt.grid(False) # Heatmap 通常不畫格線
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

# ================= 設定區 =================
DB_PATH = "msnoise.sqlite"
TARGET_PAIR = "5J_01412_5J_02050" # 指定你要畫哪一對
# =========================================

def plot_dvv_spectrum():
    if not os.path.exists(DB_PATH):
        print("❌ 找不到資料庫")
        return

    conn = sqlite3.connect(DB_PATH)

    # 1. 撈取該配對的所有濾波器結果
    # 我們需要 join 'filters' 表格來拿到頻率範圍 (low, high)
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

    # 2. 建立頻率標籤 (例如 "0.1-1.0 Hz")
    df['freq_band'] = df.apply(lambda x: f"{x['low']}-{x['high']} Hz", axis=1)
    
    # 轉換日期
    df['date'] = pd.to_datetime(df['day'])

    # 3. 檢查有幾個 Filter
    unique_filters = df['freq_band'].unique()
    print(f"🔍 發現 {len(unique_filters)} 個頻段: {unique_filters}")
    
    if len(unique_filters) < 2:
        print("⚠️ 警告：目前只有一個頻段，畫出來會像一條帶子，建議未來增加濾波器範圍。")

    # 4. 整理成矩陣格式 (Pivot Table)
    # Index=頻段(Y), Columns=日期(X), Values=dv/v
    pivot_df = df.pivot(index='freq_band', columns='date', values='dvv')
    
    # 讓頻率由高到低排列 (通常高頻看淺層畫在上面，或依個人習慣)
    # 這裡依據 frequency low bound 排序
    pivot_df = pivot_df.sort_index(key=lambda x: [float(s.split('-')[0]) for s in x])

    # 5. 畫圖
    plt.figure(figsize=(12, 6))
    
    # 使用 Seaborn 畫熱圖 (比較聰明處理 NaN 和顏色)
    # cmap='RdBu_r' : 紅色變慢(負)，藍色變快(正)，這是地震學慣例
    ax = sns.heatmap(pivot_df, cmap='RdBu_r', center=0, 
                     cbar_kws={'label': 'dv/v (%)'},
                     xticklabels=5) # X軸標籤間隔

    # 調整 X 軸日期顯示格式
    # 因為 Seaborn 會把日期變成字串，我們要手動美化一下
    date_labels = [d.strftime('%Y-%m-%d') for d in pivot_df.columns]
    ax.set_xticklabels(date_labels[::5], rotation=45) # 每5天顯示一次
    
    plt.title(f"dv/v Interferogram (Frequency vs Time): {TARGET_PAIR}")
    plt.xlabel("Date")
    plt.ylabel("Frequency Band")
    plt.tight_layout()
    plt.show()

if __name__ == "__main__":
    # 如果沒安裝 seaborn，請先 pip install seaborn
    try:
        import seaborn
        plot_dvv_spectrum()
    except ImportError:
        print("請先安裝 seaborn 函式庫: pip install seaborn")