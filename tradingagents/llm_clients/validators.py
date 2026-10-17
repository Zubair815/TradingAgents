"""Model name validators for each provider."""

from typing import Literal

from .model_catalog import DEPRECATED_MODELS, LEGACY_MODELS, MODEL_OPTIONS, get_known_models

# Providers whose model names are user-defined (local servers, relays, hosted
# OpenAI-compatible endpoints serving many models), so any model string is
# accepted without warning.
_ANY_MODEL_PROVIDERS = (
    "ollama", "openrouter", "openai_compatible",
    "mistral", "kimi", "groq", "nvidia", "bedrock",
)

VALID_MODELS = {
    provider: models
    for provider, models in get_known_models().items()
    if provider not in _ANY_MODEL_PROVIDERS
}


def validate_model(provider: str, model: str) -> bool:
    """Check if model name is valid for the given provider.

    For ollama, openrouter, and openai_compatible - any model is accepted.
    """
    provider_lower = provider.lower()

    if provider_lower in _ANY_MODEL_PROVIDERS:
        return True

    if provider_lower not in VALID_MODELS:
        return True

    return model in VALID_MODELS[provider_lower]


ModelStatus = Literal["supported", "legacy", "deprecated", "unverified", "unknown"]


def model_status(provider: str, model: str) -> ModelStatus:
    """Classify catalog confidence without claiming live provider availability."""
    provider_lower = provider.lower()
    if provider_lower in _ANY_MODEL_PROVIDERS or provider_lower not in MODEL_OPTIONS:
        return "unverified"
    if model in DEPRECATED_MODELS.get(provider_lower, []):
        return "deprecated"
    if model in LEGACY_MODELS.get(provider_lower, []):
        return "legacy"
    offered = {
        value
        for options in MODEL_OPTIONS[provider_lower].values()
        for _, value in options
    }
    return "supported" if model in offered else "unknown"
