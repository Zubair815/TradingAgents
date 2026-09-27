from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy

import tradingagents.default_config as default_config

# Use default config but allow it to be overridden
_config: dict | None = None
_active_config: ContextVar[dict | None] = ContextVar("tradingagents_config", default=None)


def build_config(config: dict | None = None) -> dict:
    """Build an isolated configuration from defaults, never another run's state."""
    result = deepcopy(default_config.DEFAULT_CONFIG)
    default_config._apply_env_overrides(result)
    _merge_config(result, config or {})
    return result



def _merge_config(target: dict, incoming: dict) -> None:
    for key, value in deepcopy(incoming).items():
        if isinstance(value, dict) and isinstance(target.get(key), dict):
            target[key].update(value)
        else:
            target[key] = value


@contextmanager
def config_scope(config: dict):
    """Use a complete run configuration only in this execution context.

    Tokens restore nesting and exceptions correctly; copied contexts in
    LangChain's tool executors keep concurrent graphs isolated as well.
    """
    token = _active_config.set(deepcopy(config))
    try:
        yield
    finally:
        _active_config.reset(token)


def initialize_config():
    """Initialize the configuration with default values."""
    global _config
    if _config is None:
        _config = deepcopy(default_config.DEFAULT_CONFIG)


def set_config(config: dict):
    """Update the configuration with custom values.

    Dict-valued keys (e.g. ``data_vendors``) are merged one level deep so a
    partial update like ``{"data_vendors": {"core_stock_apis": "alpha_vantage"}}``
    keeps the other nested keys from the default; scalar keys are replaced.
    """
    global _config
    active = _active_config.get()
    if active is not None:
        _merge_config(active, config)
    initialize_config()
    _merge_config(_config, config)


def get_config() -> dict:
    """Get the current configuration."""
    active = _active_config.get()
    if active is not None:
        return deepcopy(active)
    if _config is None:
        initialize_config()
    return deepcopy(_config)


# Initialize with default config
initialize_config()
