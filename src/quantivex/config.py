"""Provider-aware runtime settings. Credentials stay out of repr and run records."""

from __future__ import annotations

import ipaddress
import os
from dataclasses import dataclass, field, replace
from pathlib import Path
from urllib.parse import urlsplit

from .errors import ConfigError

ENV_PREFIX = "QUANTIVEX_"
PROVIDERS = ("openai", "anthropic", "ollama", "local", "openai-compatible")
DEFAULT_MODELS = {
    "openai": "gpt-4.1-mini",
    "anthropic": "",  # Choose an available Claude model explicitly.
    "ollama": "",  # The model must already be installed in the user's Ollama server.
    "local": "",
    "openai-compatible": "",
}
DEFAULT_URLS = {
    "openai": "https://api.openai.com/v1",
    "anthropic": "https://api.anthropic.com",
    "ollama": "http://localhost:11434/v1",
    "local": "http://localhost:1234/v1",
    "openai-compatible": "",
}


def data_root() -> Path:
    """Return the user-owned root for generated data, never the source tree by default."""

    configured = os.environ.get(ENV_PREFIX + "DATA_DIR")
    return Path(configured).expanduser() if configured else Path.home() / "QuantivexData"


def default_simulation_output_dir() -> Path:
    """Default destination for reports, records and simulation figures."""

    configured = os.environ.get(ENV_PREFIX + "OUTPUT_DIR")
    return Path(configured).expanduser() if configured else data_root() / "simulation_runs"


def default_results_dir() -> Path:
    """Default parent folder for `quantivex process` results folders."""

    configured = os.environ.get(ENV_PREFIX + "RESULTS_DIR")
    return Path(configured).expanduser() if configured else data_root() / "results"


def default_hardware_output_dir() -> Path:
    """Default destination for GUI-created offline IBM execution folders."""

    configured = os.environ.get(ENV_PREFIX + "HARDWARE_OUTPUT_DIR")
    return Path(configured).expanduser() if configured else data_root() / "hardware_runs"


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(ENV_PREFIX + name)
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ConfigError(f"{ENV_PREFIX}{name} must be an integer") from exc


def is_loopback(url: str) -> bool:
    host = urlsplit(url).hostname or ""
    if host.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


@dataclass(frozen=True)
class Settings:
    """Use from_env for provider defaults; validated() checks direct construction."""

    model: str = "gpt-4.1-mini"
    api_key: str | None = field(default=None, repr=False)
    base_url: str | None = None
    provider: str = "openai"
    tool_choice: str = "required"
    max_tokens: int = 4096
    max_tool_calls: int = 32
    max_rounds: int = 6
    shots: int = 4000
    seed: int = 1234
    output_dir: Path = field(default_factory=default_simulation_output_dir)
    request_timeout_s: float = 120.0
    tool_timeout_s: float = 900.0

    @classmethod
    def from_env(cls, *, validate: bool = True, **overrides: object) -> Settings:
        provider = str(overrides.get("provider") or os.environ.get(ENV_PREFIX + "PROVIDER") or "openai")
        if provider not in PROVIDERS:
            raise ConfigError(f"provider must be one of {PROVIDERS}")
        key_env, url_env = {
            "openai": ("OPENAI_API_KEY", "OPENAI_BASE_URL"),
            "anthropic": ("ANTHROPIC_API_KEY", "ANTHROPIC_BASE_URL"),
            "ollama": ("LOCAL_LLM_API_KEY", "OLLAMA_BASE_URL"),
            "local": ("LOCAL_LLM_API_KEY", "LOCAL_LLM_BASE_URL"),
            "openai-compatible": ("OPENAI_API_KEY", "OPENAI_BASE_URL"),
        }[provider]
        settings = cls(
            provider=provider,
            model=os.environ.get(ENV_PREFIX + "MODEL") or DEFAULT_MODELS[provider],
            api_key=os.environ.get(ENV_PREFIX + "API_KEY") or os.environ.get(key_env) or None,
            base_url=os.environ.get(ENV_PREFIX + "BASE_URL") or os.environ.get(url_env) or DEFAULT_URLS[provider],
            tool_choice=os.environ.get(ENV_PREFIX + "TOOL_CHOICE")
            or ("auto" if provider in ("anthropic", "ollama", "local") else "required"),
            max_tokens=_env_int("MAX_TOKENS", cls.max_tokens),
            max_tool_calls=_env_int("MAX_TOOL_CALLS", cls.max_tool_calls),
            max_rounds=_env_int("MAX_ROUNDS", cls.max_rounds),
            shots=_env_int("SHOTS", cls.shots),
            seed=_env_int("SEED", cls.seed),
            output_dir=default_simulation_output_dir(),
            request_timeout_s=float(_env_int("TIMEOUT", 120)),
            tool_timeout_s=float(_env_int("TOOL_TIMEOUT", 900)),
        )
        clean = {k: v for k, v in overrides.items() if v is not None}
        result = replace(settings, **clean)  # type: ignore[arg-type]
        return result.validated() if validate else result

    def validated(self) -> Settings:
        if self.provider not in PROVIDERS:
            raise ConfigError(f"provider must be one of {PROVIDERS}")
        if not isinstance(self.model, str) or not self.model.strip():
            raise ConfigError("Choose a model with --model or QUANTIVEX_MODEL (use the server's exact model ID)")
        if self.tool_choice not in ("auto", "required"):
            raise ConfigError("tool_choice must be 'auto' or 'required'")
        for name, lo, hi in (
            ("max_rounds", 1, 20),
            ("shots", 100, 100_000),
            ("max_tool_calls", 1, 128),
            ("max_tokens", 128, 32768),
            ("seed", 0, 2**31 - 1),
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or not lo <= value <= hi:
                raise ConfigError(f"{name} must be an integer between {lo} and {hi}")
        if not 0 < self.request_timeout_s <= 600:
            raise ConfigError("request_timeout_s must be between 0 and 600")
        if not 30 <= self.tool_timeout_s <= 3600:
            raise ConfigError("tool_timeout_s must be between 30 and 3600 seconds")
        url = self.endpoint
        try:
            parsed = urlsplit(url)
        except ValueError as exc:
            raise ConfigError("base_url is malformed") from exc
        if (
            parsed.scheme not in ("http", "https")
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
        ):
            raise ConfigError("base_url must be an HTTP(S) endpoint without credentials, query, or fragment")
        if self.provider in ("local", "ollama") and not is_loopback(url):
            raise ConfigError("Local providers require a loopback endpoint; use openai-compatible for a remote server")
        return self

    @property
    def endpoint(self) -> str:
        return (self.base_url or DEFAULT_URLS.get(self.provider, "")).rstrip("/")

    def require_api_key(self) -> str:
        if self.provider in ("local", "ollama"):
            return self.api_key or "quantivex-local"  # SDK placeholder; no cloud key is reused.
        if not self.api_key:
            variable = "ANTHROPIC_API_KEY" if self.provider == "anthropic" else "OPENAI_API_KEY"
            raise ConfigError(f"Quantivex needs an LLM credential. Set {variable} or QUANTIVEX_API_KEY.")
        return self.api_key
