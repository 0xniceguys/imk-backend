#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_DIR="$(cd -- "${SCRIPT_DIR}/.." && pwd)"
REPO_ROOT="$(cd -- "${BACKEND_DIR}/.." && pwd)"
TRAINING_DIR="${REPO_ROOT}/training"
VENV_DIR="${BACKEND_DIR}/.venv"
ENV_FILE="${BACKEND_DIR}/.env"
ENV_EXAMPLE="${BACKEND_DIR}/.env.example"

CORE_BUILD_DIR="${REPO_ROOT}/vendor/mupen64plus-core/projects/unix"
UI_BUILD_DIR="${REPO_ROOT}/vendor/mupen64plus-ui-console/projects/unix"
INPUT_PLUGIN_DIR="${REPO_ROOT}/vendor/n64train-input"
SYSTEM_PLUGIN_DIR="/usr/lib/x86_64-linux-gnu/mupen64plus"

PYTHON_BIN="${PYTHON_BIN:-python3}"
MAKE_JOBS="${MAKE_JOBS:-$(getconf _NPROCESSORS_ONLN 2>/dev/null || nproc 2>/dev/null || echo 4)}"

DB_USER="${IMK_DB_USER:-imk}"
DB_PASSWORD="${IMK_DB_PASSWORD:-imk_dev_password}"
DB_NAME="${IMK_DB_NAME:-immortalkombat}"
DB_URL="postgresql+asyncpg://${DB_USER}:${DB_PASSWORD}@localhost:5432/${DB_NAME}"

APT_PACKAGES=(
  build-essential
  git
  python3
  python3-dev
  python3-venv
  pkg-config
  curl
  ffmpeg
  xvfb
  tigervnc-standalone-server
  xdotool
  x11-utils
  lsof
  mesa-utils
  libgl1-mesa-dri
  libglu1-mesa
  libsdl2-dev
  libpng-dev
  libfreetype6-dev
  zlib1g-dev
  nasm
  postgresql
  redis-server
  mupen64plus-core
  mupen64plus-ui-console
  mupen64plus-audio-sdl
  mupen64plus-rsp-hle
  mupen64plus-video-glide64mk2
)

info() {
  printf '[dev_linux_setup] %s\n' "$*"
}

warn() {
  printf '[dev_linux_setup] WARNING: %s\n' "$*" >&2
}

die() {
  printf '[dev_linux_setup] ERROR: %s\n' "$*" >&2
  exit 1
}

need_cmd() {
  command -v "$1" >/dev/null 2>&1 || die "Required command not found: $1"
}

sudo_run() {
  if [[ "${EUID}" -eq 0 ]]; then
    "$@"
  else
    sudo "$@"
  fi
}

apt_install() {
  if [[ "${EUID}" -eq 0 ]]; then
    DEBIAN_FRONTEND=noninteractive apt-get install -y "$@"
  else
    sudo env DEBIAN_FRONTEND=noninteractive apt-get install -y "$@"
  fi
}

start_service() {
  local service_name="$1"
  if command -v systemctl >/dev/null 2>&1; then
    if sudo_run systemctl enable --now "${service_name}"; then
      return
    fi
  fi
  sudo_run service "${service_name}" start
}

postgres_exec() {
  local sql="$1"
  if [[ "${EUID}" -eq 0 ]]; then
    su postgres -c "psql -v ON_ERROR_STOP=1 -c \"$sql\""
  else
    sudo -u postgres psql -v ON_ERROR_STOP=1 -c "$sql"
  fi
}

postgres_query() {
  local sql="$1"
  if [[ "${EUID}" -eq 0 ]]; then
    su postgres -c "psql -v ON_ERROR_STOP=1 -tAc \"$sql\"" | tr -d '[:space:]'
  else
    sudo -u postgres psql -v ON_ERROR_STOP=1 -tAc "$sql" | tr -d '[:space:]'
  fi
}

postgres_createdb() {
  local db_name="$1"
  local db_owner="$2"
  if [[ "${EUID}" -eq 0 ]]; then
    su postgres -c "createdb -O \"$db_owner\" \"$db_name\""
  else
    sudo -u postgres createdb -O "$db_owner" "$db_name"
  fi
}

upsert_env() {
  local key="$1"
  local value="$2"

  if [[ ! -f "${ENV_FILE}" ]]; then
    cp "${ENV_EXAMPLE}" "${ENV_FILE}"
  fi

  if grep -q "^${key}=" "${ENV_FILE}"; then
    sed -i "s|^${key}=.*|${key}=${value}|" "${ENV_FILE}"
  else
    printf '%s=%s\n' "${key}" "${value}" >> "${ENV_FILE}"
  fi
}

build_vendor_core() {
  info "Building debugger-enabled mupen64plus core"
  make -C "${CORE_BUILD_DIR}" clean >/dev/null 2>&1 || true
  make -C "${CORE_BUILD_DIR}" -j"${MAKE_JOBS}" DEBUGGER=1

  if [[ -f "${CORE_BUILD_DIR}/libmupen64plus.so.2.0.0" && ! -e "${CORE_BUILD_DIR}/libmupen64plus.so.2" ]]; then
    ln -sf "libmupen64plus.so.2.0.0" "${CORE_BUILD_DIR}/libmupen64plus.so.2"
  fi
}

build_vendor_ui() {
  info "Building mupen64plus UI console"
  make -C "${UI_BUILD_DIR}" clean >/dev/null 2>&1 || true
  make -C "${UI_BUILD_DIR}" -j"${MAKE_JOBS}"
}

build_input_plugin() {
  info "Building Linux n64train input plugin"
  gcc \
    -Wall \
    -O2 \
    -fPIC \
    -shared \
    -o "${INPUT_PLUGIN_DIR}/n64train-input.so" \
    "${INPUT_PLUGIN_DIR}/plugin.c"
}

verify_file() {
  local path="$1"
  [[ -e "${path}" ]] || die "Missing required file: ${path}"
}

verify_executable() {
  local path="$1"
  [[ -x "${path}" ]] || die "Missing required executable: ${path}"
}

main() {
  [[ "$(uname -s)" == "Linux" ]] || die "This script must run inside a Linux VM."

  need_cmd "${PYTHON_BIN}"
  need_cmd apt-get
  need_cmd sed
  if [[ "${EUID}" -ne 0 ]]; then
    need_cmd sudo
  fi

  [[ -d "${BACKEND_DIR}" ]] || die "Backend directory not found: ${BACKEND_DIR}"
  [[ -d "${TRAINING_DIR}" ]] || die "Training directory not found: ${TRAINING_DIR}"
  [[ -f "${ENV_EXAMPLE}" ]] || die "Env example not found: ${ENV_EXAMPLE}"

  info "Installing Linux system packages"
  sudo_run apt-get update
  apt_install "${APT_PACKAGES[@]}"

  need_cmd git
  need_cmd make
  need_cmd gcc

  info "Starting PostgreSQL and Redis"
  start_service postgresql
  start_service redis-server

  info "Creating PostgreSQL role and database"
  if [[ "$(postgres_query "SELECT 1 FROM pg_roles WHERE rolname = '${DB_USER}'")" != "1" ]]; then
    postgres_exec "CREATE ROLE ${DB_USER} LOGIN PASSWORD '${DB_PASSWORD}';"
  else
    postgres_exec "ALTER ROLE ${DB_USER} WITH LOGIN PASSWORD '${DB_PASSWORD}';"
  fi

  if [[ "$(postgres_query "SELECT 1 FROM pg_database WHERE datname = '${DB_NAME}'")" != "1" ]]; then
    postgres_createdb "${DB_NAME}" "${DB_USER}"
  fi

  info "Preparing backend environment"
  mkdir -p \
    "${BACKEND_DIR}/logs" \
    "${BACKEND_DIR}/hls_output" \
    "${BACKEND_DIR}/vod_archive" \
    "${REPO_ROOT}/training/data/bridge" \
    "${REPO_ROOT}/.m64p/instances"

  upsert_env "DATABASE_URL" "${DB_URL}"
  upsert_env "REDIS_URL" "redis://localhost:6379"
  upsert_env "HLS_OUTPUT_DIR" "./hls_output"
  upsert_env "VOD_ARCHIVE_DIR" "./vod_archive"
  upsert_env "N64_TRAINING_SRC" "\"${REPO_ROOT}/training/src\""

  info "Creating backend virtual environment"
  "${PYTHON_BIN}" -m venv "${VENV_DIR}"
  "${VENV_DIR}/bin/python" -m pip install --upgrade pip setuptools wheel
  "${VENV_DIR}/bin/python" -m pip install -e "${TRAINING_DIR}"
  "${VENV_DIR}/bin/python" -m pip install -e "${BACKEND_DIR}" pytest pytest-asyncio aiosqlite

  build_vendor_core
  build_vendor_ui
  build_input_plugin

  info "Running database migrations"
  (
    cd "${BACKEND_DIR}"
    "${VENV_DIR}/bin/alembic" upgrade head
  )

  info "Verifying runtime artifacts"
  verify_executable "${VENV_DIR}/bin/uvicorn"
  verify_executable "${UI_BUILD_DIR}/mupen64plus"
  verify_file "${CORE_BUILD_DIR}/libmupen64plus.so.2"
  verify_file "${INPUT_PLUGIN_DIR}/n64train-input.so"
  verify_file "${SYSTEM_PLUGIN_DIR}/mupen64plus-video-glide64mk2.so"
  verify_file "${SYSTEM_PLUGIN_DIR}/mupen64plus-audio-sdl.so"
  verify_file "${SYSTEM_PLUGIN_DIR}/mupen64plus-rsp-hle.so"

  info "Running import smoke checks"
  (
    cd "${BACKEND_DIR}"
    "${VENV_DIR}/bin/python" -c "import app.main"
    "${VENV_DIR}/bin/python" -c "from app.services.emulator import EmulatorSession"
  )

  if [[ ! -f "${REPO_ROOT}/Mortal Kombat 4 (USA).z64" ]]; then
    warn "ROM file not found at repo root: ${REPO_ROOT}/Mortal Kombat 4 (USA).z64"
  fi

  info "Linux dev VM setup complete"
  printf '\n'
  printf 'Next steps:\n'
  printf '  cd "%s"\n' "${BACKEND_DIR}"
  printf '  source .venv/bin/activate\n'
  printf '  python3 start_backend.py --port 8000\n'
}

main "$@"
