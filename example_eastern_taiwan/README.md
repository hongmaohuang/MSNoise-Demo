# Eastern Taiwan Example

This folder provides a minimal, runnable example configuration and a quick-start guide
for a small study area in eastern Taiwan.

## Files
- `config.eastern_taiwan.json`: example configuration you can copy to `config.json`.

## Quick start
1. Copy the example config to the project root:
   ```bash
   cp example_eastern_taiwan/config.eastern_taiwan.json ./config.json
   ```
2. Use your `msnoise-hm` conda environment (or any env with MSNoise + deps installed).
3. Optionally narrow `search_criteria.networks` and `search_criteria.stations` after viewing the pre-check map.
4. Initialize the MSNoise database (from the project root):
   ```bash
   msnoise db init
   ```
5. Pre-check station availability:
   ```bash
   python pre-check-seismic-data.py \
     --min-lon 121.0 --max-lon 122.5 \
     --min-lat 22.0 --max-lat 24.8 \
     --start 2025-01-01 --end 2025-01-02
   ```
6. Run the data-prep and MSNoise pipeline:
   ```bash
   python 00_Config_setting.py
   python 01_Scan_to_DB.py
   msnoise new_jobs --init
   msnoise compute_cc
   msnoise stack -r
   msnoise reset STACK
   msnoise stack -m
   msnoise compute_mwcs
   msnoise compute_dtt
   ```

## Expected outputs (high level)
- Interactive availability map in `./pre_check_seismic_data.html`
- Station/channel availability CSV in `./pre_check_seismic_segments.csv`
- Raw daily MiniSEED in `./Seismic_Data/<NET.STA>/`
- SDS tree in `./SDS/<YEAR>/<NET>/<STA>/`
- MSNoise DB in `./msnoise.sqlite`
