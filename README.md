# MSNoise-Demo Workflow
HM Huang, 2025

This repository demonstrates a minimal MSNoise processing pipeline for converting raw seismic data into daily MiniSEED files, configuring the MSNoise database, and visualizing cross-correlation and dv/v results. All scripts share settings through a single `config.json` file at the project root.

## Project structure
- `config.json` – central configuration used by all scripts.
- `pre-check-seismic-data.py` – query FDSN station metadata before running MSNoise and build an interactive station availability map.
- `00_Config_setting.py` – download/search raw data, convert to an SDS layout, and populate the MSNoise database tables for availability.
- `01_Visualization_CC.py` – quick-look plots of cross-correlation functions (CCF) and relative velocity change (dv/v) time series.
- `02_Analysis.py` – heatmaps and additional visualizations for CCF and dv/v products.
- `config_loader.py` – helper to load the JSON configuration safely.

## Setup
- Install Python 3 with `obspy`, `numpy`, `pandas`, `matplotlib`, `seaborn`, and `folium` available.
- A conda environment is recommended for the station pre-check map:
  ```bash
  conda create -n seismic-precheck -c conda-forge python=3.11 obspy folium pandas
  ```
- Install MSNoise and initialize an empty database:
  ```bash
  msnoise db init
  ```
  Choose SQLite and leave the table prefix empty when prompted.

## Usage
1. **Pre-check station availability before downloading waveforms**
   ```bash
   conda run -n seismic-precheck python pre-check-seismic-data.py \
     --min-lon -23.6 --max-lon -20.3 \
     --min-lat 63.6 --max-lat 64.6 \
     --start 2020-01-01 --end 2026-04-15
   ```
   This queries FDSN station services directly and writes:
   - `pre_check_seismic_data.html` – an interactive map with clickable station/node markers and availability bars.
   - `pre_check_seismic_segments.csv` – the fresh station/channel availability table returned by the query.

   The default FDSN client list uses routing services plus common global and European data centers. Use `--clients` to override it:
   ```bash
   python pre-check-seismic-data.py --clients IRIS,GFZ,EIDA
   ```
   The default channel selector is `*`, which is intentionally broad enough to include DAS channels such as `HSF` and `MSF`. DAS networks are shown as individual node markers by default. Use `--collapse-das` if the map is too dense.

2. **Download, format, and scan the seismic data**
   ```bash
   python 00_Config_setting.py
   ```
   Before proceeding, run `msnoise admin` and review the parameter settings in the interface to confirm they are ready for CCF computation. For example, the stacking window.

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

4. **Visualize results**
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
- `pre-check-seismic-data.py` only checks station/channel metadata availability. It does not download waveform data. A station shown in the map still needs to be tested with waveform requests before a full MSNoise run.
- Some networks reuse station codes at different coordinates or time periods. The pre-check map splits same-code stations with different coordinates into separate markers so relocated stations are not averaged into a misleading location.
- The pre-check map uses a no-label basemap by default to reduce visual clutter. Use `--basemap osm` for the standard OpenStreetMap labels or `--basemap dark` for a dark no-label map.
- If an error occurs while running the MSNoise commands in step 3, delete `msnoise.sqlite` and `db.ini`, then rerun `msnoise db init` before repeating the workflow. If you want to skip re-downloading data, comment out `step1_search_and_download(conf)` near the end of `00_Config_setting.py` so the script resumes from the scanning stage.
- The SDS-formatted data created by step 2 lives under `seismic_processing.output_folder` (default `SDS`). Update file paths in `config.json` to match your local layout for STACKS/DTT outputs and the MSNoise database.
