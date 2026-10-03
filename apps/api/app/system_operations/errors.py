"""System operations errors: fixed messages, never input, config or exception text."""


class SystemOperationsError(Exception):
    """Base of System Operations failures."""


class SystemAccessDeniedError(SystemOperationsError):
    def __init__(self) -> None:
        super().__init__("system status access denied")
