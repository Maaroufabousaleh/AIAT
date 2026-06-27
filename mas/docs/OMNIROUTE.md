# OmniRoute gateway setup

AIAT sends model requests through this chain:

```text
AIAT workers -> LiteLLM (:4001) -> OmniRoute (:20128) -> model provider
```

LiteLLM keeps the stable AIAT model aliases and request analytics. OmniRoute
owns provider credentials, model discovery, health checks, automatic selection,
pricing metadata, and provider fallback. OmniRoute is a router, not a single
built-in model; its `auto` pool can include no-auth/free candidates such as the
OpenCode models supplied by OmniRoute.

## Automatic bootstrap

`infra/compose/mas.sh up` runs `infra/compose/configure_omniroute.py` after the
containers start. The bootstrap is idempotent and only manages connections
whose names begin with `AIAT`. It imports non-empty credentials from the root
`.env` for:

- OpenAI
- Gemini
- OpenRouter
- Groq
- Cerebras
- Mistral
- Cloudflare Workers AI (token and account ID)
- NVIDIA
- MiniMax

Providers with empty keys are intentionally skipped. In the current local
setup, OpenAI is skipped because `OPENAI_API_KEY` is empty; the compatibility
alias `gpt-4o-mini` therefore uses OmniRoute `auto` until a key is supplied.

The default AIAT model is `auto`. LiteLLM also exposes `omniroute-auto`,
`omniroute-free`, `omniroute-coding`, and `omniroute-smart`, plus compatibility
aliases for the previous gateway model names. All aliases still pass through
OmniRoute so provider health and analytics remain centralized.

Set `AIAT_OMNIROUTE_BOOTSTRAP=false` to disable bootstrap. Once OmniRoute
management authentication is enabled, create a manage-scoped key and set
`OMNIROUTE_MANAGEMENT_KEY` so the headless bootstrap remains authorized.

## Enabled OmniRoute features

- Zero-config `auto` routing and the `cheap`, `coding`, and `smart` variants.
- Provider validation and automatic rate-limit protection.
- models.dev model/capability synchronization.
- Pricing synchronization for cost-aware routing and analytics.
- 9Router installed and exposed as an additional local provider.
- CLIProxyAPI installed for optional CLI-account routing.

9Router and CLIProxyAPI are started by AIAT's bootstrap instead of OmniRoute's
internal auto-start. This avoids duplicate service supervisors and port races
during repeated Compose starts. Their switches can therefore appear off in the
OmniRoute UI even while both services are healthy.

CLIProxyAPI fallback remains disabled until CLI/OAuth accounts are explicitly
configured and its `/v1/models` endpoint returns at least one model. Do not
enable fallback while the model list is empty. AIAT does not import host OAuth
tokens automatically.

Prompt compression, MCP, and A2A are left operator-controlled. Compression can
change tool-call context, while MCP/A2A can broaden the control-plane surface;
enable them only with workload-specific tests and policy review.

## Operations

Start or reconcile the gateway:

```bash
cd mas/infra/compose
./mas.sh up omniroute litellm
```

Validate every imported provider during a manual reconciliation:

```bash
cd mas/infra/compose
python3 configure_omniroute.py --test-providers
```

Useful pages:

- LiteLLM analytics: `http://localhost:4001/ui/`
- OmniRoute analytics: `http://localhost:20128/dashboard/analytics`
- OmniRoute embedded services: `http://localhost:20128/dashboard/providers/services`

Provider secrets stay in `.env` and OmniRoute's encrypted local storage. Never
commit them or paste bootstrap/debug output that contains credentials.
