#!/bin/bash
set -euo pipefail

CONFIG_PATH="config.json"
CSV_PATH=""
OUTPUT_DIR=""
FAILED_CSV=""
TIMEOUT=""
LIMIT_TASKS=""
DATE_FROM=""
DATE_TO=""
DRY_RUN=0
STATION_PATTERNS_TEXT=""

usage() {
  cat <<'EOF'
Usage: 01_download_data.sh [options]

Download one MiniSEED file per station per day from a pre-check CSV.

Options:
  --config PATH             JSON config file
  --csv PATH                Input pre-check CSV
  --output-dir PATH         Output directory
  --failed-csv PATH         Failed-download log CSV
  --timeout SECONDS         curl timeout
  --station-pattern PAT     fnmatch pattern on NET.STA, repeatable
  --date-from YYYY-MM-DD    Optional lower day bound
  --date-to YYYY-MM-DD      Optional upper day bound
  --limit-tasks N           Cap number of station-day requests
  --dry-run                 Show planned tasks only
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --config) CONFIG_PATH="$2"; shift 2 ;;
    --csv) CSV_PATH="$2"; shift 2 ;;
    --output-dir) OUTPUT_DIR="$2"; shift 2 ;;
    --failed-csv) FAILED_CSV="$2"; shift 2 ;;
    --timeout) TIMEOUT="$2"; shift 2 ;;
    --station-pattern)
      if [[ -n "$STATION_PATTERNS_TEXT" ]]; then
        STATION_PATTERNS_TEXT="${STATION_PATTERNS_TEXT}|$2"
      else
        STATION_PATTERNS_TEXT="$2"
      fi
      shift 2
      ;;
    --date-from) DATE_FROM="$2"; shift 2 ;;
    --date-to) DATE_TO="$2"; shift 2 ;;
    --limit-tasks) LIMIT_TASKS="$2"; shift 2 ;;
    --dry-run) DRY_RUN=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown argument: $1" >&2; usage; exit 2 ;;
  esac
done

CONFIG_VALUES="$(python3 - "$CONFIG_PATH" "$CSV_PATH" "$OUTPUT_DIR" "$FAILED_CSV" "$TIMEOUT" "$DATE_FROM" "$DATE_TO" "$LIMIT_TASKS" <<'PY'
import json
import shlex
import sys

config_path, csv_arg, output_arg, failed_arg, timeout_arg, date_from_arg, date_to_arg, limit_arg = sys.argv[1:9]

try:
    with open(config_path, "r", encoding="utf-8") as handle:
        config = json.load(handle)
except FileNotFoundError:
    config = {}

search = config.get("search_criteria", {})
proc = config.get("seismic_processing", {})
precheck = config.get("precheck", {})
download = config.get("download", {})

def choose(*values):
    for value in values:
        if value not in (None, ""):
            return str(value)
    return ""

def quote(name, value):
    print(f"{name}={shlex.quote(str(value))}")

quote("CSV_PATH", choose(csv_arg, download.get("csv"), precheck.get("csv"), "pre_check_seismic_segments.csv"))
quote("OUTPUT_DIR", choose(output_arg, download.get("output_dir"), proc.get("source_folder"), "Precheck_Waveforms"))
quote("FAILED_CSV", choose(failed_arg, download.get("failed_csv"), "failed_precheck_waveform_downloads.csv"))
quote("TIMEOUT", choose(timeout_arg, download.get("timeout"), search.get("waveform_timeout"), 60))
quote("DATE_FROM", choose(date_from_arg, download.get("date_from"), search.get("start_date")))
quote("DATE_TO", choose(date_to_arg, download.get("date_to"), search.get("end_date")))
quote("LIMIT_TASKS", choose(limit_arg, download.get("limit_tasks")))
patterns = download.get("station_patterns", [])
if isinstance(patterns, str):
    patterns = [item.strip() for item in patterns.split(",") if item.strip()]
quote("CONFIG_STATION_PATTERNS", "|".join(str(item).strip() for item in patterns if str(item).strip()))
PY
)"
eval "$CONFIG_VALUES"

if [[ -z "$STATION_PATTERNS_TEXT" && -n "${CONFIG_STATION_PATTERNS:-}" ]]; then
  STATION_PATTERNS_TEXT="$CONFIG_STATION_PATTERNS"
fi

task_generator() {
  python3 - "$CSV_PATH" "$DATE_FROM" "$DATE_TO" "$LIMIT_TASKS" "$STATION_PATTERNS_TEXT" <<'PY'
import csv
import fnmatch
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta

csv_path = sys.argv[1]
date_from = datetime.strptime(sys.argv[2], "%Y-%m-%d").date() if sys.argv[2] else None
date_to = datetime.strptime(sys.argv[3], "%Y-%m-%d").date() if sys.argv[3] else None
limit_tasks = int(sys.argv[4]) if sys.argv[4] else None
patterns = [item for item in sys.argv[5].split("|") if item]

priority = {
    "GEOFON": 0,
    "GFZ": 1,
    "IRIS": 2,
    "iris-federator": 3,
    "NOA": 4,
    "ETH": 5,
    "BGR": 6,
    "INGV": 7,
    "RESIF": 8,
    "IPGP": 9,
    "KNMI": 10,
    "UIB-NORSAR": 11,
    "eida-routing": 12,
    "ORFEUS": 13,
    "ODC": 14,
}

stations = defaultdict(list)
with open(csv_path, newline="", encoding="utf-8") as handle:
    for row in csv.DictReader(handle):
        stations[(row["network"], row["station"])].append(row)

count = 0
for (network, station), rows in sorted(stations.items()):
    station_key = f"{network}.{station}"
    if patterns and not any(fnmatch.fnmatch(station_key, pat) for pat in patterns):
        continue
    start_day = min(date.fromisoformat(row["start"]) for row in rows)
    end_day = max(date.fromisoformat(row["end"]) for row in rows)
    if date_from and start_day < date_from:
        start_day = date_from
    if date_to and end_day > date_to:
        end_day = date_to
    if start_day > end_day:
        continue
    current = start_day
    while current <= end_day:
        active = [row for row in rows if date.fromisoformat(row["start"]) <= current <= date.fromisoformat(row["end"])]
        if active:
            channels = ",".join(sorted({row["channel"] for row in active}))
            locations = ",".join(sorted({"*" if row["location"] in {"", "--"} else row["location"] for row in active}))
            sources = sorted(
                {source.strip() for row in active for source in row["sources"].split(",") if source.strip()},
                key=lambda item: (priority.get(item, 999), item),
            )
            print(
                "\t".join(
                    [
                        network,
                        station,
                        current.isoformat(),
                        channels,
                        locations or "*",
                        ",".join(sources),
                    ]
                )
            )
            count += 1
            if limit_tasks is not None and count >= limit_tasks:
                sys.exit(0)
        current += timedelta(days=1)
PY
}

source_url() {
  case "$1" in
    GEOFON|GFZ) echo "https://geofon.gfz.de/fdsnws/dataselect/1/query" ;;
    IRIS|iris-federator) echo "https://service.iris.edu/fdsnws/dataselect/1/query" ;;
    ETH) echo "https://eida.ethz.ch/fdsnws/dataselect/1/query" ;;
    NOA) echo "https://eida.gein.noa.gr/fdsnws/dataselect/1/query" ;;
    BGR) echo "https://eida.bgr.de/fdsnws/dataselect/1/query" ;;
    INGV) echo "https://webservices.ingv.it/fdsnws/dataselect/1/query" ;;
    RESIF) echo "https://ws.resif.fr/fdsnws/dataselect/1/query" ;;
    IPGP) echo "https://ws.ipgp.fr/fdsnws/dataselect/1/query" ;;
    KNMI) echo "https://rdsa.knmi.nl/fdsnws/dataselect/1/query" ;;
    UIB-NORSAR) echo "https://eida.geo.uib.no/fdsnws/dataselect/1/query" ;;
    eida-routing|ORFEUS|ODC) echo "https://www.orfeus-eu.org/fdsnws/dataselect/1/query" ;;
    *) return 1 ;;
  esac
}

TASK_FILE="$(mktemp)"
task_generator > "$TASK_FILE"
TASK_COUNT="$(wc -l < "$TASK_FILE" | tr -d ' ')"
echo "Input CSV: $CSV_PATH"
echo "Planned station-day requests: $TASK_COUNT"
echo "Output directory: $OUTPUT_DIR"

if [[ "$DRY_RUN" -eq 1 ]]; then
  sed -n '1,20p' "$TASK_FILE"
  rm -f "$TASK_FILE"
  exit 0
fi

mkdir -p "$OUTPUT_DIR"

echo "network,station,date,channels,locations,sources,error" > "$FAILED_CSV"

INDEX=0
DOWNLOADED=0
EXISTS=0
FAILED=0

while IFS=$'\t' read -r NETWORK STATION DAY CHANNELS LOCATIONS SOURCES; do
  [[ -z "${NETWORK:-}" ]] && continue
  INDEX=$((INDEX + 1))
  STATION_KEY="${NETWORK}.${STATION}"
  STATION_DIR="${OUTPUT_DIR}/${STATION_KEY}"
  FILE_NAME="${STATION_KEY}.${DAY}.mseed"
  FINAL_PATH="${STATION_DIR}/${FILE_NAME}"
  mkdir -p "$STATION_DIR"
  echo "[${INDEX}/${TASK_COUNT}] ${STATION_KEY} ${DAY} channels=${CHANNELS}"
  if [[ -f "$FINAL_PATH" ]]; then
    echo "  exists"
    EXISTS=$((EXISTS + 1))
    continue
  fi
  NEXT_DAY="$(python3 - <<PY
from datetime import date, timedelta
print((date.fromisoformat("${DAY}") + timedelta(days=1)).isoformat())
PY
)"
  START_TIME="${DAY}T00:00:00"
  END_TIME="${NEXT_DAY}T00:00:00"
  SUCCESS=0
  LAST_ERROR=""
  IFS=',' read -r -a SOURCE_ARRAY <<< "$SOURCES"
  for SOURCE in "${SOURCE_ARRAY[@]}"; do
    URL_BASE="$(source_url "$SOURCE" || true)"
    [[ -z "$URL_BASE" ]] && continue
    URL="${URL_BASE}?network=${NETWORK}&station=${STATION}&location=${LOCATIONS}&channel=${CHANNELS}&starttime=${START_TIME}&endtime=${END_TIME}"
    TMP_PATH="${FINAL_PATH}.tmp"
    rm -f "$TMP_PATH"
    HTTP_CODE="$(curl -L -sS --max-time "$TIMEOUT" -o "$TMP_PATH" -w '%{http_code}' "$URL" || true)"
    if [[ "$HTTP_CODE" == "200" && -s "$TMP_PATH" ]]; then
      mv "$TMP_PATH" "$FINAL_PATH"
      echo "  downloaded via ${SOURCE}"
      DOWNLOADED=$((DOWNLOADED + 1))
      SUCCESS=1
      break
    fi
    rm -f "$TMP_PATH"
    LAST_ERROR="${SOURCE}: http=${HTTP_CODE}"
  done
  if [[ "$SUCCESS" -eq 0 ]]; then
    echo "  failed: ${LAST_ERROR}"
    printf '%s,%s,%s,%s,%s,%s,%s\n' \
      "$NETWORK" "$STATION" "$DAY" "$CHANNELS" "$LOCATIONS" "$SOURCES" "$LAST_ERROR" >> "$FAILED_CSV"
    FAILED=$((FAILED + 1))
  fi
done < "$TASK_FILE"

rm -f "$TASK_FILE"

if [[ "$FAILED" -eq 0 ]]; then
  rm -f "$FAILED_CSV"
fi

echo "Summary: downloaded=${DOWNLOADED}, exists=${EXISTS}, failed=${FAILED}"
