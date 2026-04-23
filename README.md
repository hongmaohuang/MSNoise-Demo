# MSNoise-Demo Workflow
HM Huang, 2025

This repository demonstrates a minimal MSNoise processing pipeline for checking station availability, downloading raw seismic data, converting them into an SDS layout, configuring the MSNoise database, and visualizing cross-correlation and dv/v results. All scripts share settings through a single `config.json` file at the project root.

## Project structure
- `config.json` – central configuration used by all scripts.
- `00_check_data.py` – interactive station pre-check workflow and HTML map generator.
- `01_download_data.sh` – download waveform data from a pre-check CSV.
- `02_convert_data_sds.py` – convert downloaded MiniSEED files into an SDS layout.
- `03_Scan_to_DB.py` – scan existing SDS files and populate the MSNoise database availability tables.
- `01_Visualization_CC.py` – quick-look plots of cross-correlation functions (CCF) and relative velocity change (dv/v) time series.
- `02_Analysis.py` – heatmaps and additional visualizations for CCF and dv/v products.
- `config_loader.py` – helper to load the JSON configuration safely.
- `example_eastern_taiwan/` – ready-to-run example configuration and quick-start notes for a small eastern Taiwan study area.

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
   conda run -n seismic-precheck python 00_check_data.py \
     --min-lon -23.6 --max-lon -20.3 \
     --min-lat 63.6 --max-lat 64.6 \
     --start 2020-01-01 --end 2026-04-15
   ```
   This queries FDSN station services directly and writes:
   - `pre_check_seismic_data.html` – an interactive map with clickable station/node markers and availability bars.
   - `pre_check_seismic_segments.csv` – the fresh station/channel availability table returned by the query.

   The default FDSN client list uses routing services plus common global and European data centers. Use `--clients` to override it:
   ```bash
   python 00_check_data.py --clients IRIS,GFZ,EIDA
   ```
   The default channel selector is `*`, which is intentionally broad enough to include DAS channels such as `HSF` and `MSF`. DAS networks are shown as individual node markers by default. Use `--collapse-das` if the map is too dense.

2. **Download raw waveform data from the pre-check CSV**
   ```bash
   bash 01_download_data.sh --csv pre_check_seismic_segments.csv
   ```
   This downloads one MiniSEED file per station-day into `Precheck_Waveforms/<NET.STA>/`. The script reads source nodes and time windows from the pre-check CSV, so you can edit the CSV or use `--station-pattern`, `--date-from`, and `--date-to` to customize the download scope without rerunning the station search.

3. **Convert raw MiniSEED files into SDS**
   ```bash
   python 02_convert_data_sds.py
   ```
   This reads raw files from `seismic_processing.source_folder` and writes an SDS tree into `seismic_processing.output_folder`.

4. **Scan the SDS data into the MSNoise database**
   ```bash
   python 03_Scan_to_DB.py
   ```
   Before proceeding, run `msnoise admin` and review the parameter settings in the interface to confirm they are ready for CCF computation. For example, the stacking window.

5. **Run MSNoise processing**
   ```bash
   msnoise new_jobs --init
   msnoise compute_cc
   msnoise stack -r
   msnoise reset STACK
   msnoise stack -m
   msnoise compute_mwcs
   msnoise compute_dtt
   ```

6. **Visualize results**
   ```bash
   python 01_Visualization_CC.py
   python 02_Analysis.py
   ```

## Example configuration
The `example_eastern_taiwan/` folder contains a short test configuration for eastern Taiwan. To try it from the repository root:
```bash
cp example_eastern_taiwan/config.eastern_taiwan.json ./config.json
msnoise db init
python 00_check_data.py --min-lon 121.0 --max-lon 122.5 --min-lat 22.0 --max-lat 24.8 --start 2025-01-01 --end 2025-01-02
bash 01_download_data.sh --csv pre_check_seismic_segments.csv
python 02_convert_data_sds.py
python 03_Scan_to_DB.py
```

## Configuration overview
The `config.json` file contains four main sections:
- `search_criteria`: data time range, region, FDSN clients, native FDSN selectors (`networks`, `stations`, `locations`, `channels`), and waveform timeout.
- `seismic_processing`: input/output folders used by `02_convert_data_sds.py`.
- `data_scan`: paths, station metadata, and filter/global settings used when populating MSNoise tables.
- `visualization`: file locations and plotting options for CCF and dv/v figures.

## Notes and troubleshooting
- `00_check_data.py` only checks station/channel metadata availability. It does not download waveform data.
- Some networks reuse station codes at different coordinates or time periods. The pre-check map splits same-code stations with different coordinates into separate markers so relocated stations are not averaged into a misleading location.
- The pre-check map uses a no-label basemap by default to reduce visual clutter. Use `--basemap osm` for the standard OpenStreetMap labels or `--basemap dark` for a dark no-label map.
- `01_download_data.sh` is intended to be portable. On another machine, copy `01_download_data.sh` and the pre-check CSV into the same folder, then run the shell script there.
- If an error occurs while running the MSNoise commands in step 4, delete `msnoise.sqlite` and `db.ini`, then rerun `msnoise db init` before repeating the workflow.
- If waveform data are already downloaded and converted to SDS, rerun `python 03_Scan_to_DB.py` directly instead of rerunning the download step.
- Use comma-separated FDSN selectors to limit a run after inspecting the pre-check map. For example, `networks: "TW,9L"` and `stations: "TPUB,THGS"` restrict the station search without changing the code.
- The SDS-formatted data created by step 3 lives under `seismic_processing.output_folder` (default `SDS`). Update file paths in `config.json` to match your local layout for STACKS/DTT outputs and the MSNoise database.
