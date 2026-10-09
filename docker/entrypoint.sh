#!/bin/bash
# Start Trisul AI in API mode. The container stays up without an LLM
# configuration. Settings are saved in TRISUL_AI_ENV_FILE and can be set
# in only two ways:
#   1. docker exec into the container and run `trisul_ai_cli`
#   2. pass LLM environment variables when the container is run or started
set -euo pipefail

export TRISUL_AI_HOST="${TRISUL_AI_HOST:-0.0.0.0}"
export TRISUL_AI_PORT="${TRISUL_AI_PORT:-8200}"
export TRISUL_AI_LOG_LEVEL="${TRISUL_AI_LOG_LEVEL:-info}"
export TRISUL_AI_LOG_DIR="${TRISUL_AI_LOG_DIR:-/var/log/trisul-ai}"
export TRISUL_AI_LOG_STDOUT="${TRISUL_AI_LOG_STDOUT:-1}"
export TRISUL_AI_ENV_FILE="${TRISUL_AI_ENV_FILE:-/var/lib/trisul-ai/.env}"
export MPLBACKEND="${MPLBACKEND:-Agg}"

mkdir -p "$TRISUL_AI_LOG_DIR" "$(dirname "$TRISUL_AI_ENV_FILE")"
chmod 1777 /tmp || true

python3 - <<'PY'
import os
from pathlib import Path

from dotenv import dotenv_values, set_key

path = Path(os.environ["TRISUL_AI_ENV_FILE"])
path.parent.mkdir(parents=True, exist_ok=True)
if not path.exists():
    path.touch(mode=0o600)
else:
    os.chmod(path, 0o600)

# Runtime knobs stay in the process environment. Only LLM settings are
# written into the env file so the next start can reuse them.
llm_keys = (
    "TRISUL_AI_PROVIDER",
    "TRISUL_AI_MODEL",
    "TRISUL_AI_MAX_TOKENS",
    "TRISUL_GEMINI_API_KEY",
    "TRISUL_OPENAI_API_KEY",
    "TRISUL_ANTHROPIC_API_KEY",
    "TRISUL_CUSTOM_API_KEY",
    "TRISUL_CUSTOM_API_BASE_URL",
    "TRISUL_VOYAGEAI_API_KEY",
    "TRISUL_EMBEDDING_MODEL",
    "TRISUL_EMBEDDING_PROVIDER",
)
updated = []
for key in llm_keys:
    value = os.environ.get(key)
    if value is None or not str(value).strip():
        continue
    set_key(str(path), key, value)
    updated.append(key)

config = dotenv_values(path)
provider = str(config.get("TRISUL_AI_PROVIDER") or "gemini").strip()
model = str(config.get("TRISUL_AI_MODEL") or "").strip()
if provider == "custom":
    configured = bool(model and str(config.get("TRISUL_CUSTOM_API_BASE_URL") or "").strip())
elif provider in ("gemini", "openai", "anthropic"):
    api_key = str(config.get(f"TRISUL_{provider.upper()}_API_KEY") or "").strip()
    configured = bool(model and api_key)
else:
    configured = False

if updated:
    print(
        f"Saved LLM settings to {path}: {', '.join(updated)}",
        flush=True,
    )
elif configured:
    print(f"Using saved LLM configuration from {path}", flush=True)

if not configured:
    print(
        "\n".join(
            [
                "LLM is not configured. The API is starting anyway.",
                "/api/health will succeed. A query returns \"LLM is not configured.\"",
                "",
                "Configure the LLM in one of these ways:",
                "  1. docker exec -it <container> trisul_ai_cli",
                "     Choose the model and API key, then exit.",
                "     The running API uses that saved configuration.",
                "  2. Start the container with TRISUL_AI_PROVIDER, TRISUL_AI_MODEL,",
                "     and the matching API key. Those values are saved in",
                f"     {path} and reused on the next start without them.",
                "Mount a volume on /var/lib/trisul-ai to keep that file when",
                "the container is removed and created again.",
            ]
        ),
        flush=True,
    )
PY

args=(
    api
    --host "$TRISUL_AI_HOST"
    --port "$TRISUL_AI_PORT"
    --log-level "$TRISUL_AI_LOG_LEVEL"
)

if [[ -n "${TRISUL_AI_SSL_CERTFILE:-}" || -n "${TRISUL_AI_SSL_KEYFILE:-}" ]]; then
    if [[ -z "${TRISUL_AI_SSL_CERTFILE:-}" || -z "${TRISUL_AI_SSL_KEYFILE:-}" ]]; then
        echo "HTTPS requires both TRISUL_AI_SSL_CERTFILE and TRISUL_AI_SSL_KEYFILE." >&2
        exit 1
    fi
    if [[ ! -f "$TRISUL_AI_SSL_CERTFILE" || ! -f "$TRISUL_AI_SSL_KEYFILE" ]]; then
        echo "SSL certificate or key file was not found inside the container." >&2
        exit 1
    fi
    args+=(--ssl-certfile "$TRISUL_AI_SSL_CERTFILE" --ssl-keyfile "$TRISUL_AI_SSL_KEYFILE")
fi

cd "$TRISUL_AI_LOG_DIR"
exec trisul_ai_cli "${args[@]}"
