#!/usr/bin/env bash
set -euo pipefail

SWAP_FILE=/mnt/swapfile
SWAP_GIB=${SWAP_GIB:-16}
MIN_OUT_GIB=${MIN_OUT_GIB:-32}
SWAP_DIR=$(dirname "$SWAP_FILE")

if [[ ! "$SWAP_GIB" =~ ^[1-9][0-9]*$ || ! "$MIN_OUT_GIB" =~ ^[1-9][0-9]*$ ]]; then
  echo "::error::SWAP_GIB와 MIN_OUT_GIB는 양의 정수여야 합니다."
  exit 1
fi

if [[ ! -d "$SWAP_DIR" ]]; then
  echo "::error::스왑 디렉터리가 없습니다: $SWAP_DIR"
  exit 1
fi

avail_kb=$(df -Pk "$SWAP_DIR" | awk 'NR == 2 {print $4}')
required_kb=$(((SWAP_GIB + MIN_OUT_GIB) * 1024 * 1024))
if (( avail_kb < required_kb )); then
  echo "::error::${SWAP_DIR} 여유 공간이 부족합니다. 필요: 스왑 ${SWAP_GIB} GiB + 빌드 출력 ${MIN_OUT_GIB} GiB."
  df -h "$SWAP_DIR"
  exit 1
fi

if sudo swapon --noheadings --raw --show=NAME | grep -Fxq "$SWAP_FILE"; then
  sudo swapoff "$SWAP_FILE"
fi

sudo rm -f "$SWAP_FILE"
sudo fallocate -l "${SWAP_GIB}G" "$SWAP_FILE"
sudo chmod 600 "$SWAP_FILE"
sudo mkswap "$SWAP_FILE"
sudo swapon "$SWAP_FILE"

expected_bytes=$((SWAP_GIB * 1024 * 1024 * 1024))
minimum_active_bytes=$((expected_bytes - 1024 * 1024))
active_bytes=$(sudo swapon --noheadings --raw --bytes --show=NAME,SIZE \
  | awk -v file="$SWAP_FILE" '$1 == file {print $2; exit}')
if [[ -z "$active_bytes" ]] || (( active_bytes < minimum_active_bytes )); then
  echo "::error::요청한 크기의 스왑이 활성화되지 않았습니다: ${SWAP_FILE}"
  sudo swapon --show
  exit 1
fi

remaining_kb=$(df -Pk "$SWAP_DIR" | awk 'NR == 2 {print $4}')
minimum_remaining_kb=$((MIN_OUT_GIB * 1024 * 1024))
if (( remaining_kb < minimum_remaining_kb )); then
  echo "::error::스왑 활성화 후 빌드 출력용 여유 공간이 ${MIN_OUT_GIB} GiB 미만입니다."
  df -h "$SWAP_DIR"
  exit 1
fi

free -h
df -h "$SWAP_DIR"
