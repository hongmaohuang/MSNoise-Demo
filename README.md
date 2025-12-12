# MSNoise-Demo Workflow
HM Huang, 2025

This repository demonstrates a minimal MSNoise processing pipeline for converting raw seismic data into daily MiniSEED files, configuring the MSNoise database, and visualizing cross-correlation and dv/v results. All scripts share settings through a single `config.json` file at the project root.

## Project structure
- `config.json` – central configuration used by all scripts.
- `00_Config_setting.py` – download/search raw data, convert to an SDS layout, and populate the MSNoise database tables for availability.
- `01_Visualization_CC.py` – quick-look plots of cross-correlation functions (CCF) and relative velocity change (dv/v) time series.
- `02_Analysis.py` – heatmaps and additional visualizations for CCF and dv/v products.
- `config_loader.py` – helper to load the JSON configuration safely.

## Setup
- Install Python 3 with `obspy`, `numpy`, `pandas`, `matplotlib`, and `seaborn` available.
- Install MSNoise and initialize an empty database:
  ```bash
  msnoise db init
  ```
  Choose SQLite and leave the table prefix empty when prompted.

## Usage
1. **Download, format, and scan the seismic data**
   ```bash
   python 00_Config_setting.py
   ```
   This version currently targets the Z component only. Single-station pairs via `msnoise components_to_compute_single_station` are under testing.

   Before proceeding, run `msnoise admin` and review the parameter settings in the interface to confirm they are ready for CCF computation.

2. **Run MSNoise processing**
   ```bash
   msnoise new_jobs --init
   msnoise compute_cc
   msnoise stack -r
   msnoise reset STACK
   msnoise stack -m
   msnoise compute_mwcs
   msnoise compute_dtt
   ```

3. **Visualize results**
   ```bash
   python 01_Visualization_CC.py
   python 02_Analysis.py
   ```

## Configuration overview
The `config.json` file contains three main sections:
- `seismic_processing`: input/output folders for formatting raw data.
- `data_scan`: paths, station metadata, and filter/global settings used when populating MSNoise tables.
- `visualization`: file locations and plotting options for CCF and dv/v figures.

## Notes and troubleshooting
- If an error occurs while running the MSNoise commands in step 2, delete `msnoise.sqlite` and `db.ini`, then rerun `msnoise db init` before repeating the workflow. If you want to skip re-downloading data, comment out `step1_search_and_download(conf)` near the end of `00_Config_setting.py` so the script resumes from the scanning stage.
- The SDS-formatted data created by step 1 lives under `seismic_processing.output_folder` (default `SDS`). Update file paths in `config.json` to match your local layout for STACKS/DTT outputs and the MSNoise database.
