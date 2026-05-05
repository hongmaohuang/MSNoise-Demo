# MSNoise Workflow
HM Huang, 2025

This repository is configured around one editable `config.json`. The normal workflow is:

```bash
conda run -n msnoise-hm python run_workflow.py
```

`run_workflow.py` reads `workflow.steps` from `config.json` and runs the selected steps in order.

## Project Structure

- `config.json` - central configuration for search, download, SDS conversion, MSNoise scan, and dv/v mode.
- `run_workflow.py` - main entrypoint that runs configured workflow steps.
- `src/00_check_data.py` - FDSN station pre-check and interactive HTML map.
- `src/01_download_data.sh` - waveform downloader driven by the pre-check CSV and `config.json`.
- `src/02_convert_data_sds.py` - converts downloaded MiniSEED files into SDS.
- `src/03_Scan_to_DB.py` - updates MSNoise database config, filters, stations, and data availability.
- `src/04_hourly_stack_mwcs_dvv.py` - optional hourly stack, MWCS, and dv/v workflow.
- `src/config_loader.py` - shared config loader and validator.

## Setup

Install an environment with ObsPy, Folium, pandas, numpy, and MSNoise. For example:

```bash
conda create -n msnoise-hm -c conda-forge python=3.11 obspy folium pandas numpy msnoise
conda activate msnoise-hm
msnoise db init
```

Choose SQLite and leave the table prefix empty when initializing MSNoise.

## Config-First Usage

Edit `config.json`, then run:

```bash
conda run -n msnoise-hm python run_workflow.py
```

Choose steps in:

```json
"workflow": {
  "steps": ["precheck", "download", "convert", "scan", "dvv"]
}
```

For a quick availability check only:

```json
"workflow": {
  "steps": ["precheck"]
}
```

You can still run individual scripts, but they now read `config.json` by default:

```bash
conda run -n msnoise-hm python src/00_check_data.py
bash src/01_download_data.sh --dry-run
conda run -n msnoise-hm python src/02_convert_data_sds.py
conda run -n msnoise-hm python src/03_Scan_to_DB.py
conda run -n msnoise-hm python src/04_hourly_stack_mwcs_dvv.py
```

## Key Config Sections

- `search_criteria`: date range, bounding box, FDSN clients, selectors, restricted metadata flag, and timeout.
- `precheck`: output CSV/HTML, workers, basemap, and ObsPy routing double-check settings.
- `download`: input CSV, waveform output directory, timeout, date limits, task limit, and station patterns.
- `seismic_processing`: raw waveform input folder and SDS output folder.
- `data_scan`: MSNoise database path, SDS root, filter list, and MSNoise config values such as `analysis_duration`, `keep_all`, `keep_days`, and components.
- `processing`: dv/v mode selection.
- `hourly_processing`: hourly stack/MWCS/dv/v paths and runtime options.
- `visualization`: plotting defaults.

## Station Pre-Check

`00_check_data.py` first queries FDSN station services directly with `curl`. It then optionally runs an ObsPy `RoutingClient` pass to catch networks that route through federation catalogs.

Relevant config:

```json
"precheck": {
  "csv": "pre_check_seismic_segments.csv",
  "output": "pre_check_seismic_data.html",
  "obspy_double_check": true,
  "obspy_routers": ["iris-federator", "eida-routing"]
}
```

This matters for networks such as `5S`, where the registry routes data through `NOA` even when a simple GFZ/IRIS/ORFEUS station query does not return it.

## Download

`01_download_data.sh` reads `download` from `config.json` and uses the `sources` column from the pre-check CSV to choose dataselect endpoints.

Dry-run the plan without creating output folders:

```bash
bash src/01_download_data.sh --dry-run --limit-tasks 20
```

Useful config:

```json
"download": {
  "csv": "pre_check_seismic_segments.csv",
  "output_dir": "Precheck_Waveforms",
  "date_from": "2020-01-01",
  "date_to": "2022-12-31",
  "station_patterns": []
}
```

Use `station_patterns`, for example `["5S.R7B57", "5S.R0050"]`, to restrict downloads.

## Daily vs Hourly dv/v

Choose the dv/v mode in `processing.dvv_mode`:

```json
"processing": {
  "dvv_mode": "daily"
}
```

Allowed values:

- `daily`: run the standard MSNoise daily processing commands.
- `hourly`: run `src/04_hourly_stack_mwcs_dvv.py` from existing `CROSS_CORRELATIONS`.
- `both`: run daily MSNoise processing, then hourly processing.

The hourly workflow expects MSNoise `keep_all=Y` CCF files under `CROSS_CORRELATIONS`. Set:

```json
"data_scan": {
  "analysis_duration": 3600,
  "keep_all": "Y",
  "keep_days": "Y"
}
```

Hourly output settings live in:

```json
"hourly_processing": {
  "stage": "all",
  "cc_root": "CROSS_CORRELATIONS",
  "stack_root": "HOURLY_STACKS",
  "mwcs_root": "HOURLY_MWCS",
  "dvv_root": "HOURLY_DVV",
  "txt_root": "HOURLY_TXT",
  "component": "ZZ",
  "filters": "",
  "include_all": true,
  "export_txt": true
}
```

## Notes

- `src/00_check_data.py` checks station/channel metadata only; waveform download is handled by `src/01_download_data.sh`.
- If `msnoise.sqlite` or `db.ini` becomes inconsistent, rerun `msnoise db init` before `scan`.
- `data_scan.sds_root` should be the MSNoise working directory. `seismic_processing.output_folder` is where SDS files are written.
- Command-line arguments remain available for overrides, but the intended workflow is to edit `config.json` and run `run_workflow.py`.
