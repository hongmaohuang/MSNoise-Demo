# MSNoise Demo Workflow (Revision by OpenAI Codex, also works)

This repository demonstrates a minimal MSNoise processing pipeline for converting raw seismic data into daily MiniSEED files, configuring the MSNoise database, and visualizing cross-correlation and dv/v results. All scripts now share settings through a single `config.json` file at the project root.

## Project structure
- `config.json` – central configuration used by all scripts.
- `config_loader.py` – helper to load the JSON configuration.
- `01_seismic_data_formatted_for_MSNoise.py` – converts raw `.mseed` files into SDS-formatted daily traces.
- `02_Data_Scan.py` – populates the MSNoise SQLite database and scans the SDS archive.
- `03_Visualization_CC.py` – plots CCFs and dv/v alongside well pressure data.

## Setup
1. Install the Python dependencies used by the scripts (e.g., `obspy`, `numpy`, `pandas`, `matplotlib`).
2. Review and adjust paths and parameters in `config.json` to match your environment.
3. Place raw seismic waveform files in the directory specified by `seismic_processing.source_folder`.

## Usage
1. **Format seismic data**
   ```bash
   python 01_seismic_data_formatted_for_MSNoise.py
   ```
   This reads raw MiniSEED files and writes SDS-structured daily files to `seismic_processing.output_folder`.

2. **Initialize the MSNoise database**
   ```bash
   msnoise db init
   ```
   Choose SQLite and leave the table prefix empty when prompted.

3. **Configure and scan data**
   ```bash
   python 02_Data_Scan.py
   ```
   This writes configuration entries, inserts station metadata, and scans the SDS archive under `data_scan.sds_root`.

4. **Run MSNoise processing**
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
   python 03_Visualization_CC.py
   ```
   Figures are saved to the folder defined by `visualization.figs_folder`.

## Configuration overview
The `config.json` file contains three sections:
- `seismic_processing`: input/output folders for formatting raw data.
- `data_scan`: paths, station metadata, and filter/global settings used when populating MSNoise tables.
- `visualization`: file locations and plotting options for CCF and dv/v figures.

Adjust these values as needed; all three scripts will pick up your changes automatically.
