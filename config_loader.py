import json
import os
from typing import Any, Dict, Iterable


def load_config(file_path: str = "config.json") -> Dict[str, Any]:
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"Configuration file not found: {file_path}")

    with open(file_path, "r", encoding="utf-8") as f:
        try:
            return json.load(f)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid JSON in {file_path}: {exc}") from exc


def _require_keys(cfg: Dict[str, Any], path: str, keys: Iterable[str]) -> None:
    missing = [key for key in keys if key not in cfg]
    if missing:
        raise KeyError(f"Missing keys at {path}: {', '.join(missing)}")


def validate_config(cfg: Dict[str, Any]) -> None:
    """Validate the config sections needed by the data-prep workflow."""

    _require_keys(cfg, "root", ["search_criteria", "seismic_processing", "data_scan"])

    search = cfg["search_criteria"]
    _require_keys(search, "search_criteria", ["start_date", "end_date", "region"])
    region = search["region"]
    _require_keys(region, "search_criteria.region", ["min_lat", "max_lat", "min_lon", "max_lon"])

    proc = cfg["seismic_processing"]
    _require_keys(proc, "seismic_processing", ["source_folder", "output_folder"])

    scan = cfg["data_scan"]
    _require_keys(scan, "data_scan", ["sds_root", "db_path", "filter_config"])
