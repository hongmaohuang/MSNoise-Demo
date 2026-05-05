#!/usr/bin/env python3
"""Run configured MSNoise workflow steps from one config file."""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent
SRC_DIR = PROJECT_ROOT / "src"
sys.path.insert(0, str(SRC_DIR))

from config_loader import load_config, validate_config


VALID_STEPS = {"precheck", "download", "convert", "scan", "dvv"}


def run_command(command: list[str]) -> None:
    print("\n$ " + " ".join(command))
    subprocess.run(command, check=True, cwd=PROJECT_ROOT)


def configured_steps(config: dict) -> list[str]:
    workflow = config.get("workflow", {})
    raw_steps = workflow.get("steps", ["precheck"])
    if isinstance(raw_steps, str):
        raw_steps = [item.strip() for item in raw_steps.split(",") if item.strip()]
    steps = [str(step).strip() for step in raw_steps if str(step).strip()]
    invalid = [step for step in steps if step not in VALID_STEPS]
    if invalid:
        raise ValueError(f"Invalid workflow.steps: {', '.join(invalid)}")
    return steps


def daily_commands(config: dict) -> list[list[str]]:
    processing = config.get("processing", {})
    commands = processing.get("daily_commands")
    if commands:
        return [command if isinstance(command, list) else str(command).split() for command in commands]
    return [
        ["msnoise", "new_jobs", "--init"],
        ["msnoise", "compute_cc"],
        ["msnoise", "stack", "-r"],
        ["msnoise", "reset", "STACK"],
        ["msnoise", "stack", "-m"],
        ["msnoise", "compute_mwcs"],
        ["msnoise", "compute_dtt"],
    ]


def run_dvv(config: dict, config_path: Path, python: str) -> None:
    processing = config.get("processing", {})
    mode = str(processing.get("dvv_mode", "daily")).strip().lower()
    if mode not in {"both", "daily", "hourly"}:
        raise ValueError("processing.dvv_mode must be one of: both, daily, hourly")
    if mode in {"both", "daily"}:
        for command in daily_commands(config):
            run_command(command)
    if mode in {"both", "hourly"}:
        hourly = config.get("hourly_processing", {})
        stage = str(hourly.get("stage", "all"))
        command = [python, str(SRC_DIR / "04_hourly_stack_mwcs_dvv.py"), "--config", str(config_path), "--stage", stage]
        if hourly.get("force", False):
            command.append("--force")
        run_command(command)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run workflow steps from config.json.")
    parser.add_argument("--config", default="config.json", help="Workflow JSON config file.")
    parser.add_argument(
        "--steps",
        default="",
        help="Optional comma-separated override: precheck,download,convert,scan,dvv.",
    )
    args = parser.parse_args(argv)

    config_path = Path(args.config).expanduser()
    if not config_path.is_absolute():
        config_path = PROJECT_ROOT / config_path
    config = load_config(str(config_path))
    validate_config(config)
    steps = [item.strip() for item in args.steps.split(",") if item.strip()] if args.steps else configured_steps(config)
    invalid = [step for step in steps if step not in VALID_STEPS]
    if invalid:
        raise ValueError(f"Invalid steps: {', '.join(invalid)}")

    python = sys.executable
    for step in steps:
        if step == "precheck":
            run_command([python, str(SRC_DIR / "00_check_data.py"), "--config", str(config_path)])
        elif step == "download":
            run_command(["bash", str(SRC_DIR / "01_download_data.sh"), "--config", str(config_path)])
        elif step == "convert":
            run_command([python, str(SRC_DIR / "02_convert_data_sds.py"), "--config", str(config_path)])
        elif step == "scan":
            run_command([python, str(SRC_DIR / "03_Scan_to_DB.py"), "--config", str(config_path)])
        elif step == "dvv":
            run_dvv(config, config_path, python)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
