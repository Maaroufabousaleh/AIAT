"""Governed OpenHands v1.43 model-name compatibility.

AIAT model identities are provider-agnostic aliases.  OpenHands v1.43 sends
the configured model through LiteLLM, whose provider resolver requires an
explicit provider prefix for a custom OpenAI-compatible endpoint.  Keep that
wire detail at the OpenHands boundary; it must not become a task-selectable
provider or change the AIAT model registry identity.
"""

from __future__ import annotations

AIAT_OPENHANDS_MODEL_ID = "omniroute-coding"
OPENHANDS_WIRE_PROVIDER = "openai"
OPENHANDS_WIRE_MODEL_ID = f"{OPENHANDS_WIRE_PROVIDER}/{AIAT_OPENHANDS_MODEL_ID}"


def wire_model_id_for(logical_model_id: str) -> str:
    """Return the only v1.43 wire model allowed for the governed alias.

    The strict equality check is intentional.  A caller cannot use this
    compatibility helper to select another provider/model or to smuggle an
    arbitrary model string into an Agent Server profile.
    """

    if logical_model_id != AIAT_OPENHANDS_MODEL_ID:
        raise ValueError("OpenHands requires the governed omniroute-coding model alias")
    return OPENHANDS_WIRE_MODEL_ID


def is_expected_wire_model(model_id: object) -> bool:
    """Whether a server readback is exactly the governed v1.43 wire value."""

    return model_id == OPENHANDS_WIRE_MODEL_ID
