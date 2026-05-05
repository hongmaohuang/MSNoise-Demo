import json
import os
from typing import Any, Dict, Iterable, Optional


PATH_FIELDS = (
    ("seismic_processing", "source_folder"),
    ("seismic_processing", "output_folder"),
    ("data_scan", "sds_root"),
    ("data_scan", "db_path"),
    ("data_scan", "metadata_csv"),
    ("visualization", "figs_folder"),
)


def resolve_path(path: Optional[str], base_dir: Optional[str] = None) -> Optional[str]:
    """Resolve a config path relative to the config file directory."""
    if path in (None, ""):
        return path
    text = str(path)
    if text == "your MSNoise working directory":
        return text
    expanded = os.path.expanduser(text)
    if os.path.isabs(expanded):
        return os.path.normpath(expanded)
    root = base_dir or os.getcwd()
    return os.path.normpath(os.path.join(root, expanded))


def normalize_paths(cfg: Dict[str, Any], base_dir: str) -> Dict[str, Any]:
    for section, key in PATH_FIELDS:
        section_cfg = cfg.get(section)
        if isinstance(section_cfg, dict) and key in section_cfg:
            section_cfg[key] = resolve_path(section_cfg.get(key), base_dir)
    return cfg


def load_config(file_path: str = "config.json") -> Dict[str, Any]:
    config_path = os.path.abspath(os.path.expanduser(file_path))
    if not os.path.exists(config_path):
        raise FileNotFoundError(f"Configuration file not found: {config_path}")

    with open(config_path, "r", encoding="utf-8") as f:
        try:
            cfg = json.load(f)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid JSON in {config_path}: {exc}") from exc
    cfg["_config_path"] = config_path
    cfg["_config_dir"] = os.path.dirname(config_path)
    return normalize_paths(cfg, cfg["_config_dir"])


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
