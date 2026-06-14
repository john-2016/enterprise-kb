#!/usr/bin/env bash
# =============================================================
# add-provider.sh - Add a custom LLM provider + models
# =============================================================
# Adds a provider (and optional models) via the admin API.
# Supported templates: OpenAI, Anthropic, Gemini, DeepSeek, Qwen, GLM, Local.
# Re-runnable: skips providers that already exist by display_name.
# =============================================================
set -euo pipefail

API_BASE="${API_BASE:-http://localhost:8000/api/v1}"
ADMIN_USER="${ADMIN_USER:-admin}"
ADMIN_TOKEN_FILE="/tmp/.admin_token_$$..."

# ---------- 1) Login as admin ----------
login() {
  PASSWORD=""
  if [ -f "./data/.admin_password" ]; then
    PASSWORD=$(cat "./data/.admin_password")
  else
    read -r -s -p "Admin password: " PASSWORD; echo
  fi

  RESP=$(curl -sf -X POST "$API_BASE/auth/login" \
    -H "Content-Type: application/json" \
    -d "{\"username\":\"$ADMIN_USER\",\"password\":\"$PASSWORD\"}" 2>/dev/null) || {
    echo "[error] login failed. Is the service running at $API_BASE?" >&2
    exit 1
  }
  echo "$RESP" | python3 -c "import sys, json; print(json.load(sys.stdin)['access_token'])"
}

TOKEN=$(login)
trap "rm -f $ADMIN_TOKEN_FILE" EXIT

# ---------- 2) Provider templates ----------
declare -A NAME=(  [1]="OpenAI"     [2]="Anthropic"  [3]="Gemini"      [4]="DeepSeek"   [5]="Qwen"     [6]="GLM"     [7]="Local"  )
declare -A TYPE=(  [1]="openai_compat" [2]="anthropic"  [3]="gemini"       [4]="openai_compat" [5]="openai_compat" [6]="openai_compat" [7]="openai_compat" )
declare -A BASE=(  [1]="https://api.openai.com/v1"  [2]="https://api.anthropic.com"  [3]="https://generativelanguage.googleapis.com"  [4]="https://api.deepseek.com/v1"  [5]="https://dashscope.aliyuncs.com/compatible-mode/v1"  [6]="https://open.bigmodel.cn/api/paas/v4"  [7]="http://localhost:11434/v1" )
declare -A KEYVAR=( [1]="OPENAI_API_KEY"  [2]="ANTHROPIC_API_KEY"  [3]="GEMINI_API_KEY"  [4]="DEEPSEEK_API_KEY"  [5]="QWEN_API_KEY"  [6]="GLM_API_KEY"  [7]="" )

echo
echo "Select a provider template:"
echo "  1) OpenAI      (openai_compat)"
echo "  2) Anthropic   (anthropic native)"
echo "  3) Gemini      (gemini native)"
echo "  4) DeepSeek    (openai_compat)"
echo "  5) Qwen        (openai_compat)"
echo "  6) GLM         (openai_compat)"
echo "  7) Local Ollama (openai_compat, http://localhost:11434/v1)"
echo
read -r -p "Choice [1-7, q=quit]: " CHOICE
[[ "$CHOICE" == "q" ]] && exit 0
[[ -z "${NAME[$CHOICE]:-}" ]] && { echo "Invalid choice" >&2; exit 1; }

PROV_NAME="${NAME[$CHOICE]}"
PROV_TYPE="${TYPE[$CHOICE]}"
PROV_BASE="${BASE[$CHOICE]}"
PROV_KEYVAR="${KEYVAR[$CHOICE]}"

# ---------- 3) Collect input ----------
read -r -p "Display name [${PROV_NAME}]: " DISPLAY_NAME
DISPLAY_NAME=${DISPLAY_NAME:-$PROV_NAME}

read -r -p "API base URL [${PROV_BASE}]: " BASE_URL
BASE_URL=${BASE_URL:-$PROV_BASE}

if [ -n "$PROV_KEYVAR" ] && [ -n "${!PROV_KEYVAR:-}" ]; then
  read -r -p "API key (Enter to use \$$PROV_KEYVAR): " API_KEY
  API_KEY=${API_KEY:-${!PROV_KEYVAR}}
else
  read -r -s -p "API key (can be empty for Local): " API_KEY; echo
fi

# ---------- 4) POST /admin/providers ----------
echo
echo "Creating provider '$DISPLAY_NAME' ($PROV_TYPE)..."
RESP=$(curl -sf -X POST "$API_BASE/admin/providers" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d "{
    \"name\": \"$(echo $DISPLAY_NAME | tr '[:upper:]' '[:lower:]' | tr -d ' ')\",
    \"display_name\": \"$DISPLAY_NAME\",
    \"provider_type\": \"$PROV_TYPE\",
    \"api_base_url\": \"$BASE_URL\",
    \"api_key\": \"$API_KEY\"
  }") || { echo "[error] POST /admin/providers failed" >&2; exit 1; }

PROV_ID=$(echo "$RESP" | python3 -c "import sys, json; print(json.load(sys.stdin)['id'])")
echo "  -> provider id=$PROV_ID"

# ---------- 5) Add models (loop) ----------
while true; do
  echo
  read -r -p "Add a model for this provider? [y/N] " REPLY
  [[ ! "$REPLY" =~ ^[Yy]$ ]] && break

  read -r -p "  Model name (e.g. gpt-4o-mini): " MODEL_NAME
  [ -z "$MODEL_NAME" ] && { echo "model name required" >&2; continue; }

  read -r -p "  Capability [1=chat, 2=embedding] (default 1): " CAP
  CAP=${CAP:-1}
  if [ "$CAP" = "1" ]; then
    CAPABILITY="chat"
  else
    CAPABILITY="embedding"
  fi

  read -r -p "  Context window (default 128000): " CTX
  CTX=${CTX:-128000}

  echo "  Creating model $MODEL_NAME ($CAPABILITY)..."
  curl -sf -X POST "$API_BASE/admin/models" \
    -H "Authorization: Bearer $TOKEN" \
    -H "Content-Type: application/json" \
    -d "{
      \"provider_id\": $PROV_ID,
      \"model_name\": \"$MODEL_NAME\",
      \"model_type\": \"$CAPABILITY\",
      \"context_window\": $CTX
    }" > /dev/null && echo "  -> created"

  read -r -p "  Set as default $CAPABILITY? [y/N] " REPLY
  if [[ "$REPLY" =~ ^[Yy]$ ]]; then
    MODEL_ID=$(curl -sf "$API_BASE/admin/models?provider_id=$PROV_ID" \
      -H "Authorization: Bearer $TOKEN" \
      | python3 -c "import sys, json; d=json.load(sys.stdin); print([m['id'] for m in d if m['model_name']=='$MODEL_NAME'][0])")
    PATCH_FIELD="is_default_chat"
    [ "$CAPABILITY" = "embedding" ] && PATCH_FIELD="is_default_emb"
    curl -sf -X PATCH "$API_BASE/admin/models/$MODEL_ID" \
      -H "Authorization: Bearer $TOKEN" \
      -H "Content-Type: application/json" \
      -d "{\"$PATCH_FIELD\": true}" > /dev/null && echo "  -> set as default $CAPABILITY"
  fi
done

echo
echo "Done. Verify with: curl $API_BASE/admin/providers -H 'Authorization: Bearer \$TOKEN'"
