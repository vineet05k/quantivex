"""Exception hierarchy. Every error Quantivex raises on purpose is a QuantivexError."""


class QuantivexError(Exception):
    """Base class for all Quantivex errors."""


class DataValidationError(QuantivexError, ValueError):
    """A calibration snapshot does not match the documented schema."""


class HardwareError(QuantivexError, ValueError):
    """A qubit-selection request cannot be satisfied on this chip."""


class ChemistryError(QuantivexError, ValueError):
    """A molecule, geometry or active space is unsupported or unavailable."""


class ConfigError(QuantivexError, RuntimeError):
    """Required configuration (for example the LLM API key) is missing or invalid."""


class AgentError(QuantivexError, RuntimeError):
    """The LLM provider failed or returned something unusable."""
