#!/bin/sh
set -eu

: "${OPENCODE_SERVER_PASSWORD:?OPENCODE_SERVER_PASSWORD is required}"
: "${AIAT_GATEWAY_URL:?AIAT_GATEWAY_URL is required}"
: "${AIAT_GATEWAY_API_KEY:?AIAT_GATEWAY_API_KEY is required}"

# The generated file lives on the disposable runtime tmpfs.  It is never
# copied to the image or returned in an AIAT event/evidence payload.
umask 077
cat > "${OPENCODE_CONFIG}" <<EOF
{
  "\$schema": "https://opencode.ai/config.json",
  "provider": {
    "aiat": {
      "npm": "@ai-sdk/openai-compatible",
      "name": "AIAT gateway",
      "options": {
        "baseURL": "${AIAT_GATEWAY_URL}/v1",
        "apiKey": "${AIAT_GATEWAY_API_KEY}"
      },
      "models": {
        "omniroute-coding": {
          "id": "omniroute-coding",
          "name": "AIAT governed coding model",
          "tool_call": true,
          "reasoning": false,
          "temperature": true,
          "limit": {
            "context": 32768,
            "output": 8192
          }
        }
      }
    }
  }
}
EOF
exec opencode serve --hostname "${OPENCODE_SERVER_HOSTNAME}" --port "${OPENCODE_SERVER_PORT}"
