#!/usr/bin/env bash
# Linux / AutoDL 批量：对齐 video-asset-pipeline 的 batch_stage_a 习惯。
# - 子进程设置 RAW_INGEST_INPUT_ROOT=当前扫描目录（输出镜像子路径）
# - 支持 BATCH_RECURSE=1 递归子目录
# - 不扫描输入树内的 raw-ingest 产出区及 RAW_INGEST_OUTPUT_ROOT（若在输入下）
# - 额外参数原样传给 `python -m video_raw_ingest run`（如 --replace --whisperx-model small）
#
# 用法：
#   export RAW_INGEST_OUTPUT_ROOT=/root/autodl-tmp/raw-ingest
#   ./tools/batch_ingest.sh
#   BATCH_RECURSE=1 ./tools/batch_ingest.sh /path/to/root
#   ./tools/batch_ingest.sh /path/to/videos --replace

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export PYTHONUNBUFFERED="${PYTHONUNBUFFERED:-1}"

if [[ -x "$ROOT/.venv/bin/python" ]]; then
  PYTHON="$ROOT/.venv/bin/python"
elif [[ -x "$ROOT/.venv/bin/python3" ]]; then
  PYTHON="$ROOT/.venv/bin/python3"
else
  PYTHON="${PYTHON:-python3}"
fi

MOD="video_raw_ingest"
INPUT="${RAW_INGEST_BATCH_INPUT:-}"
if [[ $# -ge 1 && -d "$1" ]]; then
  INPUT="$1"
  shift
fi
if [[ -z "$INPUT" ]]; then
  if [[ -d "/root/autodl-tmp" ]]; then
    INPUT="/root/autodl-tmp"
  else
    INPUT="$ROOT/data/input"
  fi
fi
INPUT="$(cd "$INPUT" && pwd)"

if [[ ! -d "$INPUT" ]]; then
  echo "Input directory does not exist: $INPUT" >&2
  exit 1
fi

SKIP_RAW_INGEST=""
if [[ -d "$INPUT/raw-ingest" ]]; then
  SKIP_RAW_INGEST="$(cd "$INPUT/raw-ingest" && pwd)"
fi

SKIP_OUTPUT_UNDER_INPUT=""
if [[ -n "${RAW_INGEST_OUTPUT_ROOT:-}" && -d "${RAW_INGEST_OUTPUT_ROOT}" ]]; then
  odr="$(cd "$RAW_INGEST_OUTPUT_ROOT" && pwd)"
  case "$odr" in
    "$INPUT"|"$INPUT"/*)
      SKIP_OUTPUT_UNDER_INPUT="$odr"
      ;;
  esac
fi

_batch_skip_listed_file() {
  local f="$1"
  if [[ -n "$SKIP_RAW_INGEST" ]]; then
    case "$f" in
      "$SKIP_RAW_INGEST"|"$SKIP_RAW_INGEST"/*) return 0 ;;
    esac
  fi
  if [[ -n "$SKIP_OUTPUT_UNDER_INPUT" ]]; then
    case "$f" in
      "$SKIP_OUTPUT_UNDER_INPUT"|"$SKIP_OUTPUT_UNDER_INPUT"/*) return 0 ;;
    esac
  fi
  return 1
}

shopt -s nullglob
mapfile -t files < <(
  {
    if [[ "${BATCH_RECURSE:-0}" == "1" ]]; then
      find "$INPUT" -type f \( \
        -iname '*.mp4' -o -iname '*.mkv' -o -iname '*.mov' -o -iname '*.webm' -o \
        -iname '*.avi' -o -iname '*.m4v' -o -iname '*.flv' -o -iname '*.wmv' \) | sort
    else
      find "$INPUT" -maxdepth 1 -type f \( \
        -iname '*.mp4' -o -iname '*.mkv' -o -iname '*.mov' -o -iname '*.webm' -o \
        -iname '*.avi' -o -iname '*.m4v' -o -iname '*.flv' -o -iname '*.wmv' \) | sort
    fi
  } | while IFS= read -r f; do
    if _batch_skip_listed_file "$f"; then
      continue
    fi
    printf '%s\n' "$f"
  done
)

if [[ ${#files[@]} -eq 0 ]]; then
  echo "No video files under: $INPUT"
  exit 0
fi

echo "InputDir: $INPUT"
if [[ -n "${RAW_INGEST_OUTPUT_ROOT:-}" ]]; then
  echo "RAW_INGEST_OUTPUT_ROOT: $RAW_INGEST_OUTPUT_ROOT"
fi
if [[ -n "$SKIP_RAW_INGEST" ]]; then
  echo "Skip scan under: $SKIP_RAW_INGEST"
fi
if [[ -n "$SKIP_OUTPUT_UNDER_INPUT" && "$SKIP_OUTPUT_UNDER_INPUT" != "$SKIP_RAW_INGEST" ]]; then
  echo "Skip scan under: $SKIP_OUTPUT_UNDER_INPUT"
fi
echo "Files: ${#files[@]}"
echo ""

ok=0
fail=0
i=0
n=${#files[@]}
for f in "${files[@]}"; do
  i=$((i + 1))
  echo "[$i/$n] $f"
  if RAW_INGEST_INPUT_ROOT="$INPUT" "$PYTHON" -m "$MOD" run "$f" "$@"; then
    ok=$((ok + 1))
  else
    fail=$((fail + 1))
    echo "FAILED: $f" >&2
    if [[ "${BATCH_STOP_ON_FAIL:-0}" == "1" ]]; then
      exit 1
    fi
  fi
  echo ""
done

echo "Done: OK=$ok FAIL=$fail"
[[ "$fail" -eq 0 ]]
