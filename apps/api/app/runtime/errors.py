"""Configuration errors raised while building the agent runtime."""


class RuntimeConfigurationError(RuntimeError):
    """Raised when required agent runtime configuration is missing or invalid."""


class ModelConfigurationError(RuntimeConfigurationError):
    """Raised when the default model provider is selected but incompletely configured."""
