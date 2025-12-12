# MSNoise-Demo Workflow
HM Huang, 2025

This repository demonstrates a minimal MSNoise processing pipeline for converting raw seismic data into daily MiniSEED files, configuring the MSNoise database, and visualizing cross-correlation and dv/v results. All scripts now share settings through a single `config.json` file at the project root.

## Project structure
- `config.json` – central configuration used by all scripts.
- `config_loader.py` – helper to load the JSON configuration.
- `00_Config_setting.py` – automated script for downloading, formatting (SDS), and scanning data.
- `01_Visualization_CC.py` – basic visualization for CCF and dv/v.
- `02_Analysis.py` – advanced analysis (Heatmaps).

## Usage

### 1. Initialize the MSNoise database
```bash
msnoise db init