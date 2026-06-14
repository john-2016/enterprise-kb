#!/usr/bin/env bash
# =============================================================
# Enterprise Knowledge Base - One-command installer
# =============================================================
# Target: 5 minutes from `git clone` to working service.
# Requires: docker, docker compose, curl.
# Generates .env (with random secrets), starts postgres + app,
# waits for health, prints admin credentials.
# =============================================================
set -euo pipefail

# Colors (only if stdout is a terminal)
if [ -t 1 ]; then
  C_GREEN='\033[0;32m'
  C_YELLOW='\033[1;33m'
  C_RED='\033[0;31m'
  C_RESET='\033[0m'
else
  C_GREEN=''; C_YELLOW=''; C_RED=''; C_RESET=''
fi

log()  { printf "${C_GREEN}[install]${C_RESET} %s\n" "$*"; }
warn() { printf "${C_YELLOW}[warn]${C_RESET}    %s\n" "$*" >&2; }
err()  { printf "${C_RED}[error]${C_RESET}   %s\n" "$*" >&2; exit 1; }

# ---------- 1) Pre-flight checks ----------
command -v docker      >/dev/null 2>&1 || err "docker not installed"
command -v docker compose >/dev/null 2>&1 || command -v "docker-compose" >/dev/null 2>&1 \
  || err "docker compose plugin not installed"
command -v curl        >/dev/null 2>&1 || err "curl not installed"

# ---------- 2) Generate .env if missing ----------
if [ ! -f .env ]; then
  log "Generating .env from .env.example..."

  cp .env.example .env

  # Random secrets
  JWT_SECRET=$(python3 -c "import secrets; print(secrets.token_urlsafe(48))")
  ENCRYPT_KEY=$(python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())")

  # Replace empty placeholders in .env
  sed -i "s|^JWT_SECRET_KEY=.*|JWT_SECRET_KEY=${JWT_SECRET}|" .env
  sed -i "s|^ENCRYPTION_KEY=.*|ENCRYPTION_KEY=${ENCRYPT_KEY}|" .env

  log "Wrote JWT_SECRET_KEY and ENCRYPTION_KEY (random)"
else
  log ".env already exists, leaving it alone"
fi

# ---------- 3) Ask for additional providers (optional) ----------
echo
log "Do you want to add a custom LLM provider now? (you can also do this later via ./scripts/add-provider.sh)"
read -r -p "Add a provider? [y/N] " REPLY
if [[ "$REPLY" =~ ^[Yy]$ ]]; then
  bash scripts/add-provider.sh
fi

# ---------- 4) Start services ----------
log "Starting docker compose (postgres + app)..."
docker compose up -d --build

# ---------- 5) Wait for health ----------
log "Waiting for service to become healthy (this can take ~60s on first build)..."
for i in $(seq 1 60); do
  if curl -sf http://localhost:8000/api/v1/health >/dev/null 2>&1; then
    log "Service is up!"
    break
  fi
  sleep 2
done

if ! curl -sf http://localhost:8000/api/v1/health >/dev/null 2>&1; then
  err "Service did not become healthy in 120s. Check: docker compose logs app"
fi

# ---------- 6) Read admin password (random on first boot) ----------
echo
PASSWORD_FILE="./data/.admin_password"
if [ -f "$PASSWORD_FILE" ]; then
  ADMIN_PWD=$(cat "$PASSWORD_FILE")
else
  ADMIN_PWD="admin123  (default, file .admin_password missing - change immediately)"
fi

cat <<EOF

${C_GREEN}============================================${C_RESET}
${C_GREEN} ✓ Enterprise Knowledge Base is running${C_RESET}
${C_GREEN}============================================${C_RESET}

  URL:           http://localhost:8000
  Admin user:    admin
  Admin pass:    ${ADMIN_PWD}
  Swagger UI:    http://localhost:8000/docs

  First-time tips:
   - Log in and change the admin password immediately.
   - Add more providers via: ./scripts/add-provider.sh
   - The built-in 'minimax' provider is auto-seeded.

EOF
