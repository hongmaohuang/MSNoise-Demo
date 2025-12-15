import os
import json
import glob
import sqlite3
import numpy as np
import pandas as pd
import obspy
from obspy import UTCDateTime
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
import seaborn as sns

plt.rcParams['font.family'] = 'Nimbus Sans'  
plt.rcParams['font.size'] = 13

class MSNoiseVisualizer:
    def __init__(self, config_path="config.json"):
        self.config = self._load_config(config_path)
        self.pair_name = self._get_pair_name()
        
        self.cc_base_dir = self.config['visualization']['cc_files']
        self.dtt_target_dir = self.config['visualization']['dtt_folder']
        self.db_path = self.config['data_scan']['db_path']
        self.figs_output = self.config['visualization']['figs_folder']
        
        os.makedirs(self.figs_output, exist_ok=True)

    def _load_config(self, path):
        with open(path, 'r') as f:
            return json.load(f)

    def _get_pair_name(self):
        s1 = self.config['visualization']['station1'].replace("-", "_")
        s2 = self.config['visualization']['station2'].replace("-", "_")
        stations = sorted([s1, s2])
        return f"{stations[0]}_{stations[1]}"

    def plot_ccf_heatmap(self):
        pair_folder = os.path.join(self.cc_base_dir, self.pair_name)
        
        if not os.path.exists(pair_folder):
            print(f"No Folder: {pair_folder}")
            return

        print(f"Reading CCF from: {pair_folder} ...")
        file_list = sorted([f for f in os.listdir(pair_folder) if f.endswith(".MSEED")])
        
        if not file_list:
            print("The folder is empty")
            return

        data_matrix = []
        dates = []
        lags = None
        max_lag = 60.0 

        for fname in file_list:
            path = os.path.join(pair_folder, fname)
            try:
                st = obspy.read(path)
                tr = st[0]
                
                npts = tr.stats.npts
                samprate = tr.stats.sampling_rate
                t_axis = np.linspace(-((npts-1)/2)/samprate, ((npts-1)/2)/samprate, npts)
                
                mask = (t_axis >= -max_lag) & (t_axis <= max_lag)
                cut_data = tr.data[mask]
                
                if lags is None:
                    lags = t_axis[mask]
                
                cut_data = cut_data / np.max(np.abs(cut_data))
                
                data_matrix.append(cut_data)
                
                date_str = fname.replace(".MSEED", "")
                dates.append(UTCDateTime(date_str).datetime)
                
            except Exception as e:
                print(f"Skipping {fname}: {e}")

        if not data_matrix:
            return

        matrix = np.array(data_matrix).T 
        date_nums = mdates.date2num(dates)

        fig, ax = plt.subplots(figsize=(10, 6))
        im = ax.imshow(matrix, aspect='auto', cmap='seismic', 
                       extent=[date_nums[0], date_nums[-1], lags[0], lags[-1]],
                       vmin=-1, vmax=1, interpolation='nearest')

        ax.xaxis_date()
        ax.xaxis.set_major_formatter(mdates.DateFormatter('%Y-%m-%d'))
        fig.autofmt_xdate()

        plt.title(f"CCF Temporal Evolution: {self.pair_name}")
        plt.ylabel("Lapse Time (s)")
        plt.xlabel("Date")
        plt.colorbar(im, label="Normalized Amplitude")
        plt.tight_layout()
        
        out_file = os.path.join(self.figs_output, f"CCF_{self.pair_name}.png")
        plt.savefig(out_file, dpi=300)
        print(f"CCF Plot saved to {out_file}")

    def _get_filter_mapping(self):
        if not os.path.exists(self.db_path):
            print("Can't find the database file: {self.db_path}")
            return None

        try:
            conn = sqlite3.connect(self.db_path)
            df = pd.read_sql_query("SELECT ref, low, high FROM filters", conn)
            conn.close()
            mapping = {}
            for _, row in df.iterrows():
                fmt_ref = f"{int(row['ref']):02d}"
                mapping[fmt_ref] = f"{row['low']:.2f}-{row['high']:.2f} Hz"
            return mapping
        except Exception as e:
            print(f"failed on reading filters: {e}")
            return None

    def plot_dvv_heatmap(self):
        filter_map = self._get_filter_mapping()
        
        dtt_root = os.path.abspath(os.path.join(self.dtt_target_dir, "../../.."))
        
        print(f"Scanning DTT root: {dtt_root} for pair {self.pair_name}...")
        
        target_stack = self.dtt_target_dir.split(os.sep)[-2] 
        component = self.dtt_target_dir.split(os.sep)[-1]    

        all_data = []
        filter_dirs = sorted(glob.glob(os.path.join(dtt_root, "*"))) 

        for f_dir in filter_dirs:
            filter_id = os.path.basename(f_dir)
            if not filter_id.isdigit():
                continue

            if filter_map and filter_id in filter_map:
                freq_label = filter_map[filter_id]
            else:
                freq_label = f"Filter {filter_id}"

            target_path = os.path.join(f_dir, target_stack, component, "*.txt")
            files = sorted(glob.glob(target_path))

            for txt_file in files:
                try:
                    df_day = pd.read_csv(txt_file)
                    target_row = df_day[df_day['Pairs'] == self.pair_name]
                    
                    if not target_row.empty:
                        val = target_row.iloc[0]['M'] * 100 
                        date_str = target_row.iloc[0]['Date']
                        
                        all_data.append({
                            'date': pd.to_datetime(date_str),
                            'freq_band': freq_label,
                            'dvv': val,
                            'filter_sort_key': float(filter_map[filter_id].split('-')[0]) if filter_map else int(filter_id)
                        })
                except Exception:
                    continue

        if not all_data:
            print("No data found for the specified pair and filters.")
            return

        df_all = pd.DataFrame(all_data)
        
        pivot_df = df_all.pivot(index='freq_band', columns='date', values='dvv')
        
        sort_map = df_all[['freq_band', 'filter_sort_key']].drop_duplicates().set_index('freq_band')['filter_sort_key']
        pivot_df = pivot_df.sort_index(key=lambda x: x.map(sort_map))

        plt.figure(figsize=(12, 6))
        ax = sns.heatmap(pivot_df, cmap='RdBu_r', center=0, 
                         cbar_kws={'label': 'dv/v (%)'},
                         xticklabels=10) 

        date_labels = [d.strftime('%Y-%m-%d') for d in pivot_df.columns]
        ax.set_xticklabels(date_labels[::10], rotation=45) 
        
        for _, spine in ax.spines.items():
            spine.set_visible(True)
            spine.set_linewidth(1.0)
            spine.set_color('black')
        
        ax.set_axisbelow(False) 
        ax.grid(True, which='both', color='gray', linestyle='--', linewidth=0.5, alpha=0.5)

        cbar = ax.collections[0].colorbar
        cbar.outline.set_visible(True)
        cbar.outline.set_linewidth(1)
        cbar.outline.set_edgecolor('black')

        plt.title(f"dv/v Interferogram: {self.pair_name}")
        plt.xlabel("Date")
        plt.ylabel("Frequency Band")
        plt.tight_layout()
        
        out_file = os.path.join(self.figs_output, f"dvv_{self.pair_name}.png")
        plt.savefig(out_file, dpi=300)
        print(f"dv/v Plot saved to {out_file}")

if __name__ == "__main__":
    viz = MSNoiseVisualizer("config.json")
    
    print("--- 1. Plotting CCF Heatmap ---")
    viz.plot_ccf_heatmap()
    
    print("\n--- 2. Plotting dv/v Heatmap ---")
    viz.plot_dvv_heatmap()