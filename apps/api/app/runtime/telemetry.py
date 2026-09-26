"""Agno telemetry policy: disabled, fail closed.

Agno 3.0.11 overrides each Agent's ``telemetry`` flag from ``AGNO_TELEMETRY``
whenever that variable exists (at initialization and again on each run), so
``Agent(telemetry=False)`` alone does not guarantee telemetry is off. The product
therefore:

* builds every Agent and AgentOS with ``telemetry=False``; and
* refuses to start unless ``AGNO_TELEMETRY`` is unset or ``false``.

An explicit attempt to enable telemetry is rejected rather than silently rewritten.
"""

import os
from collections.abc import Mapping

from app.runtime.errors import RuntimeConfigurationError

TELEMETRY_ENV_VAR = "AGNO_TELEMETRY"


def enforce_telemetry_policy(environ: Mapping[str, str] = os.environ) -> None:
    value = environ.get(TELEMETRY_ENV_VAR)
    if value is None or value.strip().lower() == "false":
        return
    raise RuntimeConfigurationError(
        "Agno telemetry is disabled by product policy: "
        f"{TELEMETRY_ENV_VAR} must be unset or 'false'."
    )
