#!/usr/bin/env bash
# One-command local run: checks prerequisites, runs lint/type/tests, starts the stack, opens the UI.
#
#   ./start.sh                 checks + tests + build + start + open browser
#   ./start.sh --skip-tests    skip lint, type checks and tests (backend and web)
#   ./start.sh --no-build      reuse the existing localhost/rag-mvp and localhost/rag-web images
#   ./start.sh --no-browser    don't open the browser
#   ./start.sh test            checks + infrastructure + tests only
#   ./start.sh stop            stop the stack (data volumes are kept)
set -euo pipefail

cd "$(dirname "$0")"

UI_URL="http://localhost:3000"
LEGACY_UI_URL="http://localhost:8501"
API_URL="http://localhost:8000"
PROJECT="rag-mvp"
INFRA=(postgres qdrant objectstore mailpit)

run_tests=1 build=1 browser=1 mode=all
for arg in "$@"; do
  case "$arg" in
    --skip-tests) run_tests=0 ;;
    --no-build) build=0 ;;
    --no-browser) browser=0 ;;
    test) mode="test" ;;
    stop) mode="stop" ;;
    -h | --help) sed -n '2,9p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "Unknown argument: $arg (see ./start.sh --help)" >&2; exit 2 ;;
  esac
done

if [[ -t 1 ]]; then B=$'\e[1m' G=$'\e[32m' Y=$'\e[33m' R=$'\e[31m' N=$'\e[0m'; else B="" G="" Y="" R="" N=""; fi
step() { printf '\n%s==> %s%s\n' "$B" "$*" "$N"; }
ok() { printf '%s  ok%s %s\n' "$G" "$N" "$*"; }
warn() { printf '%s  warn%s %s\n' "$Y" "$N" "$*"; }
die() { printf '%s  error%s %s\n' "$R" "$N" "$*" >&2; exit 1; }

compose() { podman compose "$@" 2> >(grep -v 'Executing external compose provider' >&2); }

# --- Podman ---------------------------------------------------------------------
step "Checking Podman"
command -v podman >/dev/null || die "Podman is not installed. Install it from https://podman.io and re-run."
if ! podman info >/dev/null 2>&1; then
  if [[ "$(uname -s)" == "Darwin" || "$(uname -s)" == MINGW* ]] && podman machine list --format '{{.Name}}' 2>/dev/null | grep -q .; then
    warn "Podman machine is not running; starting it"
    podman machine start >/dev/null || die "Could not start the Podman machine. Run 'podman machine start' and check its output."
    podman info >/dev/null 2>&1 || die "Podman is still not reachable after starting the machine."
  elif [[ "$(uname -s)" == "Darwin" ]]; then
    die "No Podman machine found. Run 'podman machine init && podman machine start', then re-run."
  else
    die "Podman is installed but not reachable. Start the Podman service (e.g. 'systemctl --user start podman.socket')."
  fi
fi
ok "Podman $(podman version --format '{{.Client.Version}}') is running"
podman compose version >/dev/null 2>&1 || die "'podman compose' needs a compose provider. Install docker-compose or podman-compose."
ok "podman compose is available"

if [[ "$mode" == stop ]]; then
  step "Stopping the stack"
  compose down
  ok "Stopped. Data volumes are kept; 'podman compose down -v' deletes them."
  exit 0
fi

# --- .env -------------------------------------------------------------------------
step "Checking .env"
secret() { openssl rand -hex 32; }
if [[ ! -f .env ]]; then
  cp .env.example .env
  chmod 600 .env
  pg_pass=$(secret)
  sed -i.bak \
    -e "s|^JWT_SECRET=.*|JWT_SECRET=$(secret)|" \
    -e "s|^POSTGRES_PASSWORD=.*|POSTGRES_PASSWORD=${pg_pass}|" \
    -e "s|^QDRANT_API_KEY=.*|QDRANT_API_KEY=$(secret)|" \
    -e "s|^S3_SECRET_KEY=.*|S3_SECRET_KEY=$(secret)|" \
    -e "s|^SESSION_SECRET=.*|SESSION_SECRET=$(secret)|" \
    -e "s|rag:CHANGE_ME@|rag:${pg_pass}@|" \
    .env
  rm -f .env.bak
  ok "Created .env with generated secrets (git-ignored, mode 600). Add provider keys there if you want cloud models."
else
  ok ".env exists"
  # .env files created before the Next.js UI lack its settings; add them without touching anything else.
  if ! grep -q '^SESSION_SECRET=.' .env; then
    sed -i.bak '/^SESSION_SECRET=/d' .env && rm -f .env.bak
    printf '\n# Web UI (Next.js BFF)\nSESSION_SECRET=%s\n' "$(secret)" >>.env
    ok "Added a generated SESSION_SECRET to .env"
  fi
  if grep -q '^PUBLIC_UI_URL=http://localhost:8501' .env; then
    sed -i.bak 's|^PUBLIC_UI_URL=http://localhost:8501|PUBLIC_UI_URL=http://localhost:3000|' .env && rm -f .env.bak
    ok "Pointed PUBLIC_UI_URL (email links) at the web UI"
  fi
fi
set -a
# shellcheck disable=SC1091
. ./.env
set +a
for key in JWT_SECRET POSTGRES_PASSWORD QDRANT_API_KEY S3_SECRET_KEY SESSION_SECRET; do
  [[ -n "${!key:-}" ]] || die "$key is empty in .env"
done
ok "Required secrets are set"

# --- Ports ------------------------------------------------------------------------
step "Checking ports"
PG_PORT="${POSTGRES_HOST_PORT:-5432}"
ports=("$PG_PORT" 6333 9000 "${MAILPIT_SMTP_PORT:-1025}" "${MAILPIT_UI_PORT:-8025}")
[[ "$mode" == all ]] && ports+=(8000 8501 3000)
ours=$(podman ps --filter "label=com.docker.compose.project=${PROJECT}" --format '{{.Ports}}' 2>/dev/null || true)
busy=0
for port in "${ports[@]}"; do
  # Podman collapses adjacent ports into ranges, e.g. 127.0.0.1:6333-6334->6333-6334/tcp.
  if grep -qE "127\.0\.0\.1:${port}(-[0-9]+)?->" <<<"$ours"; then
    continue
  fi
  if (exec 3<>"/dev/tcp/127.0.0.1/${port}") 2>/dev/null; then
    warn "Port ${port} is already in use by another process or project"
    busy=1
  fi
done
[[ "$busy" == 0 ]] || die "Free those ports, or set the overrides in .env (POSTGRES_HOST_PORT, MAILPIT_UI_PORT, MAILPIT_SMTP_PORT)."
ok "Ports are free or already owned by ${PROJECT}"

# --- Ollama (optional) ------------------------------------------------------------
step "Checking Ollama"
if tags=$(curl -sf -m 3 "${OLLAMA_BASE_URL:-http://localhost:11434}/api/tags"); then
  ok "Ollama is reachable"
  for model in qwen3.5:4b bge-m3 "${OLLAMA_VISION_MODEL:-qwen3-vl:latest}"; do
    grep -q "\"${model%%:latest}" <<<"$tags" || warn "Model ${model} is not pulled: ollama pull ${model}"
  done
else
  warn "Ollama is not reachable. Tests still run, but local chat, embeddings and receipt extraction need it:"
  warn "  OLLAMA_HOST=0.0.0.0 ollama serve   (see README, 'Ollama and containers')"
fi

# --- Infrastructure ---------------------------------------------------------------
wait_for() {
  local name=$1 timeout=$2
  shift 2
  local deadline=$((SECONDS + timeout))
  until "$@" >/dev/null 2>&1; do
    ((SECONDS < deadline)) || die "${name} did not become ready within ${timeout}s. Check: podman compose logs ${name}"
    sleep 2
  done
  ok "${name} is ready"
}

step "Starting infrastructure (${INFRA[*]})"
compose up -d "${INFRA[@]}"
wait_for postgres 90 compose exec -T postgres pg_isready -U "${POSTGRES_USER:-rag}" -d "${POSTGRES_DB:-rag}"
wait_for qdrant 90 curl -sf http://localhost:6333/healthz
wait_for objectstore 90 curl -sf http://localhost:9000/health
wait_for mailpit 60 curl -sf "http://localhost:${MAILPIT_UI_PORT:-8025}/api/v1/info"

# --- Tests ------------------------------------------------------------------------
if [[ "$run_tests" == 1 ]]; then
  step "Running lint, type checks and tests"
  command -v uv >/dev/null || die "uv is not installed. Install it from https://docs.astral.sh/uv/ or use --skip-tests."
  uv sync --extra dev --frozen -q
  uv run ruff check src tests ui evals
  uv run ruff format --check src tests ui evals
  uv run mypy src
  # Point the integration suite at this project's containers, whatever DATABASE_URL in .env says.
  pg_pass_q=$(uv run python -c 'import sys, urllib.parse; print(urllib.parse.quote(sys.argv[1], safe=""))' "$POSTGRES_PASSWORD")
  TEST_DATABASE_URL="postgresql+asyncpg://${POSTGRES_USER:-rag}:${pg_pass_q}@localhost:${PG_PORT}/${POSTGRES_DB:-rag}" \
    QDRANT_URL=http://localhost:6333 S3_ENDPOINT_URL=http://localhost:9000 SMTP_PORT="${MAILPIT_SMTP_PORT:-1025}" \
    uv run pytest -q
  if command -v npm >/dev/null; then
    (cd web && npm ci --no-audit --no-fund --silent && npm run -s lint && npm run -s typecheck && npm run -s format:check && npm test --silent)
  else
    warn "npm is not installed; skipping web checks (the web image still builds in a container)"
  fi
  ok "All checks passed"
fi
[[ "$mode" == test ]] && exit 0

# --- App --------------------------------------------------------------------------
if [[ "$build" == 1 ]]; then
  step "Building localhost/rag-mvp:latest"
  podman build -q -t localhost/rag-mvp:latest -f Containerfile . >/dev/null
  podman build -q -t localhost/rag-web:latest -f web/Containerfile web >/dev/null
  ok "Images built"
fi

step "Starting api, worker, web and legacy ui"
compose up -d --no-build
# The API applies migrations and waits (up to 120s) for local model warmup before it reports healthy.
wait_for api 240 curl -sf "${API_URL}/healthz"
wait_for web 120 curl -sf -o /dev/null "${UI_URL}/login"
wait_for ui 120 curl -sf "${LEGACY_UI_URL}/_stcore/health"
curl -sf "${API_URL}/readyz" >/dev/null || warn "API /readyz reports a dependency down: curl ${API_URL}/readyz"

step "Ready"
cat <<EOF
  UI       ${UI_URL}
  Legacy   ${LEGACY_UI_URL}   (Receipts, Members, Settings until they move to the web UI)
  API docs ${API_URL}/docs
  Mailpit  http://localhost:${MAILPIT_UI_PORT:-8025}   (signup verification, invites, password resets)

  First time? Use "New here? Sign up" on the sign-in page, or create an admin:
    podman compose exec api rag admin create --email you@example.com --tenant demo
  Logs:  podman compose logs -f api worker
  Stop:  ./start.sh stop
EOF

if [[ "$browser" == 1 ]]; then
  if command -v open >/dev/null; then open "$UI_URL"
  elif command -v xdg-open >/dev/null; then xdg-open "$UI_URL" >/dev/null 2>&1 &
  else warn "Open ${UI_URL} in your browser"
  fi
fi
