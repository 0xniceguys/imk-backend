#!/usr/bin/env bash
set -euo pipefail
export PATH="$HOME/.cargo/bin:$HOME/.local/share/solana/install/active_release/bin:$HOME/.npm-global/bin:$PATH"

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
ENV_FILE="${ENV_FILE:-$ROOT_DIR/deployment/devnet/.env.devnet}"

if [[ -f "$ENV_FILE" ]]; then
  set -a
  source "$ENV_FILE"
  set +a
fi

required=(bash curl jq)
required_deploy=(anchor solana solana-keygen spl-token yarn)

missing=()
for cmd in "${required[@]}"; do
  command -v "$cmd" >/dev/null 2>&1 || missing+=("$cmd")
done

missing_deploy=()
for cmd in "${required_deploy[@]}"; do
  command -v "$cmd" >/dev/null 2>&1 || missing_deploy+=("$cmd")
done

echo "[info] base prerequisites"
if [[ ${#missing[@]} -gt 0 ]]; then
  printf '  missing: %s\n' "${missing[*]}"
  exit 1
else
  echo "  ok"
fi

echo "[info] deploy prerequisites"
if [[ ${#missing_deploy[@]} -gt 0 ]]; then
  printf '  missing: %s\n' "${missing_deploy[*]}"
  echo "  note: key generation can still work via Python+solders fallback"
else
  echo "  ok"
fi

if command -v python3 >/dev/null 2>&1; then
  if python3 - <<'PY' >/dev/null 2>&1
import importlib.util, sys
sys.exit(0 if importlib.util.find_spec('solders') else 1)
PY
  then
    echo "[info] python solders: available"
  elif [[ -x /home/ubuntu/imk/.venv/bin/python ]]; then
    if /home/ubuntu/imk/.venv/bin/python - <<'PY' >/dev/null 2>&1
import importlib.util, sys
sys.exit(0 if importlib.util.find_spec('solders') else 1)
PY
    then
      echo "[info] python solders: available in /home/ubuntu/imk/.venv"
    else
      echo "[warn] python solders not found"
    fi
  else
    echo "[warn] python solders not found"
  fi
fi

echo "[done] prerequisite check complete"
