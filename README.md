# MSNoise-Demo Workflow
HM Huang, 2025

This repository demonstrates a minimal MSNoise processing pipeline for converting raw seismic data into daily MiniSEED files, configuring the MSNoise database, and visualizing cross-correlation and dv/v results. All scripts now share settings through a single `config.json` file at the project root.

## Project structure
- `config.json` – central configuration used by all scripts.
- `config_loader.py` – helper to load the JSON configuration.

## Setup

## Usage
1. **Initialize the MSNoise database**
   ```bash
   msnoise db init
   ```
   Choose SQLite and leave the table prefix empty when prompted.

2. **Download, Format, and Scan the seismic data**
   ```bash
   python 00_Config_setting.py
   ```
   Now this version is only for Z component, so stay tuned for single-station pairs CCF feat. "msnoise components_to_compute_single_station". It is under testing now.
     
   Before getting into the next section, please use: msnoise admin, and direct to the parameter setting interface to confirm the settings are OK to compute your CCF.

3. **Run MSNoise processing**
   ```bash
   msnoise new_jobs --init
   msnoise compute_cc
   msnoise stack -r
   msnoise reset STACK
   msnoise stack -m
   msnoise compute_mwcs
   msnoise compute_dtt
   ```

5. **Visualize results**
   ```bash
   python 01_Visualization_CC.py
   python 02_analysis
   ```

## Configuration overview
The `config.json` file contains three sections:
- `seismic_processing`: input/output folders for formatting raw data.
- `data_scan`: paths, station metadata, and filter/global settings used when populating MSNoise tables.
- `visualization`: file locations and plotting options for CCF and dv/v figures.

Note: 如果在msnoise指令下發生錯誤 in the 3rd step，你就要把msnoise.sqlite跟db.ini刪掉，然後從msnoise db init開始來過，如果不想重新下載資料，在執行00_Config_setting.py之前可以到裡面把倒數幾行的        step1_search_and_download(conf)註解就會只從Scan開始做, which saves your time, for sure.


 