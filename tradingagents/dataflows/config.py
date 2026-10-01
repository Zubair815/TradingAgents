import json
from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy
from pathlib import Path

import tradingagents.default_config as default_config

# Use default config but allow it to be overridden
_config: dict | None = None
_active_config: ContextVar[dict | None] = ContextVar("tradingagents_config", default=None)
_RUNTIME_CONFIG_PATH = Path.home() / ".tradingagents" / "runtime_settings.json"
_SECRET_KEY_PATTERNS = ("api_key", "token", "secret", "password", "passwd")


def _is_secret_like_key(key: str) -> bool:
    normalized = str(key).lower()
    return any(pattern in normalized for pattern in _SECRET_KEY_PATTERNS)


def _load_runtime_settings() -> dict:
    if not _RUNTIME_CONFIG_PATH.exists():
        return {}
    try:
        data = json.loads(_RUNTIME_CONFIG_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError, TypeError):
        return {}


def _validate_runtime_value(key: str, value):
    if value is None:
        return None
    if _is_secret_like_key(key):
        raise ValueError(f"Refusing to persist secret-like setting {key!r}; keep it in env vars or process memory.")
    if key not in default_config.DEFAULT_CONFIG:
        raise ValueError(f"Unsupported runtime setting: {key!r}")

    if key == "forex_default_pair":
        if not isinstance(value, str):
            raise ValueError(f"{key} must be a string")
        from tradingagents.forex.domain import get_forex_pair

        pair = get_forex_pair(value.upper().replace("/", "").replace("-", "").replace("_", ""))
        if pair is None:
            raise ValueError(f"{key} must be a supported Forex pair")
        return pair.symbol
    if key == "forex_default_execution_timeframe":
        if not isinstance(value, str):
            raise ValueError(f"{key} must be a string")
        from tradingagents.forex.domain import Timeframe

        return Timeframe.from_string(value).value
    if key == "forex_default_context_timeframes":
        if not isinstance(value, list) or not value:
            raise ValueError(f"{key} must be a non-empty list")
        from tradingagents.forex.domain import Timeframe

        normalized = [Timeframe.from_string(item).value for item in value]
        if len(set(normalized)) != len(normalized):
            raise ValueError(f"{key} must not contain duplicates")
        return normalized
    if key == "forex_market_source":
        if value not in {"mt5", "yahoo"}:
            raise ValueError(f"{key} must be 'mt5' or 'yahoo'")
        return value
    if key == "forex_default_risk_percent":
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"{key} must be numeric")
        value = float(value)
        if not 0 < value <= 5:
            raise ValueError(f"{key} must be greater than 0 and at most 5")
        return value
    if key == "forex_min_rr":
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"{key} must be numeric")
        value = float(value)
        if value <= 0:
            raise ValueError(f"{key} must be greater than 0")
        return value
    if key == "forex_news_blackout_minutes":
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError(f"{key} must be an integer")
        if value < 0:
            raise ValueError(f"{key} must be >= 0")
        return value
    if key == "mt5_poll_interval_seconds":
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"{key} must be numeric")
        value = float(value)
        if value < 0.5:
            raise ValueError(f"{key} must be at least 0.5 seconds")
        return value

    reference = default_config.DEFAULT_CONFIG.get(key)
    explicit_type = default_config._CONFIG_KEY_TYPES.get(key)
    if key in {"max_tokens", "llm_max_retries"}:
        explicit_type = int
    if explicit_type is bool:
        if not isinstance(value, bool):
            raise ValueError(f"{key} must be a boolean")
        return value
    if explicit_type is int:
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError(f"{key} must be an integer")
        if key in {"max_debate_rounds", "max_risk_discuss_rounds", "news_article_limit", "global_news_article_limit", "global_news_lookback_days", "holding_period_days"} and value < 1:
            raise ValueError(f"{key} must be at least 1")
        return value
    if explicit_type is float:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"{key} must be numeric")
        value = float(value)
        if key == "temperature" and not 0.0 <= value <= 2.0:
            raise ValueError(f"{key} must be between 0 and 2")
        if key in {"forex_max_spread_pips"} and value < 0:
            raise ValueError(f"{key} must be >= 0")
        return value
    if explicit_type is str:
        if not isinstance(value, str):
            raise ValueError(f"{key} must be a string")
        if key == "backend_url" and value and not value.startswith(("http://", "https://")):
            raise ValueError(f"{key} must be an http(s) URL when provided")
        return value
    if isinstance(reference, bool):
        if not isinstance(value, bool):
            raise ValueError(f"{key} must be a boolean")
        return value
    if isinstance(reference, int):
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError(f"{key} must be an integer")
        if key in {"max_debate_rounds", "max_risk_discuss_rounds", "news_article_limit", "global_news_article_limit", "global_news_lookback_days", "holding_period_days"} and value < 1:
            raise ValueError(f"{key} must be at least 1")
        return value
    if isinstance(reference, float):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"{key} must be numeric")
        value = float(value)
        if key == "temperature" and not 0.0 <= value <= 2.0:
            raise ValueError(f"{key} must be between 0 and 2")
        if key in {"forex_max_spread_pips"} and value < 0:
            raise ValueError(f"{key} must be >= 0")
        return value
    if isinstance(reference, str):
        if not isinstance(value, str):
            raise ValueError(f"{key} must be a string")
        if key == "backend_url" and value and not value.startswith(("http://", "https://")):
            raise ValueError(f"{key} must be an http(s) URL when provided")
        return value
    if isinstance(reference, list):
        if not isinstance(value, list):
            raise ValueError(f"{key} must be a list")
        return value
    if isinstance(reference, dict):
        if not isinstance(value, dict):
            raise ValueError(f"{key} must be an object")
        return value
    return value


def save_runtime_settings(patch: dict) -> dict:
    """Persist non-secret runtime overrides to the local settings file and apply them."""
    if not isinstance(patch, dict):
        raise ValueError("Runtime settings must be submitted as an object")

    global _config
    merged = deepcopy(_load_runtime_settings())
    for key, value in patch.items():
        merged[key] = _validate_runtime_value(str(key), value)

    _RUNTIME_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    _RUNTIME_CONFIG_PATH.write_text(json.dumps(merged, indent=2, sort_keys=True), encoding="utf-8")
    _config = build_config()
    return get_config()


def reset_runtime_settings() -> dict:
    """Clear any local runtime overrides and return the default config state."""
    global _config
    _config = deepcopy(default_config.DEFAULT_CONFIG)
    if _RUNTIME_CONFIG_PATH.exists():
        _RUNTIME_CONFIG_PATH.unlink()
    return deepcopy(_config)


def build_config(config: dict | None = None) -> dict:
    """Build an isolated configuration from defaults, never another run's state."""
    result = deepcopy(default_config.DEFAULT_CONFIG)
    _merge_config(result, _load_runtime_settings())
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
    """Initialize the configuration with default values, plus any local runtime overrides."""
    global _config
    if _config is None:
        _config = deepcopy(default_config.DEFAULT_CONFIG)
        _merge_config(_config, _load_runtime_settings())


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
    runtime = _load_runtime_settings()
    if runtime:
        merged = deepcopy(default_config.DEFAULT_CONFIG)
        _merge_config(merged, runtime)
        _merge_config(merged, _config or {})
        return deepcopy(merged)
    return deepcopy(_config)


# Initialize with default config
initialize_config()
