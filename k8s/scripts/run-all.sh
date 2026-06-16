#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

echo "===== STARTING MASTER DEPLOYMENT ====="

mapfile -t SCRIPTS < <(
  find "${SCRIPT_DIR}" -maxdepth 1 -type f -name "*.sh" | sort
)

for script in "${SCRIPTS[@]}"; do
  SCRIPT_NAME="$(basename "$script")"

  if [[ "$SCRIPT_NAME" == "run-all.sh" ]]; then
    continue
  fi

  echo
  echo "=================================================="
  echo "RUNNING: ${SCRIPT_NAME}"
  echo "=================================================="

  sed -i 's/\r$//' "$script"
  chmod +x "$script"
  bash "$script"

  echo
  echo "COMPLETED: ${SCRIPT_NAME}"
done

echo
echo "===== ALL SCRIPTS COMPLETED SUCCESSFULLY ====="
