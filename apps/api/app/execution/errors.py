"""Product-owned execution errors raised by trusted action handlers.

Handlers translate provider/transport failures into one of these, stating whether
the external side effect may have happened. Messages are never audited.
"""


class ActionInputError(Exception):
    """Raw parameters could not be validated into the handler's input (no side effect)."""


class ActionExecutionError(Exception):
    """Execution failed. ``effect_may_have_occurred`` decides the outcome:

    - False: the handler can establish that nothing changed        -> FAILED (no verify)
    - True:  the outcome is uncertain (e.g. timeout after sending) -> verify with no
             receipt, then REQUIRES_HUMAN
    """

    effect_may_have_occurred: bool = True

    def __init__(self, message: str = "action execution failed") -> None:
        super().__init__(message)


class ExecutionFailedWithoutEffect(ActionExecutionError):
    """The handler confirmed that no side effect occurred."""

    effect_may_have_occurred = False


class ExecutionOutcomeUncertain(ActionExecutionError):
    """The side effect may or may not have occurred."""

    effect_may_have_occurred = True
