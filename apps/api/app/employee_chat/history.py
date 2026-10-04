"""The bounded, deterministic multi-turn context policy of Employee Chat.

The Product owns the history; the Agent receives at most:

- the last ``HISTORY_MAX_TURNS`` (12) COMPLETED turns of the SAME thread before the
  current one (pending and failed turns never become context), oldest first;
- at most ``HISTORY_MAX_CHARS`` (24 000) characters of user + assistant text in total.
  Turns are taken newest first while they fit; the first turn that does not fit stops
  the selection (older turns are dropped whole, never cut in the middle).

History is conversation CONTEXT only: it never carries or changes identity,
permissions, company, store or policy, and current facts still come from the Product
tools.
"""

from collections.abc import Iterable

from app.employee_chat.models import (
    HISTORY_MAX_CHARS,
    HISTORY_MAX_TURNS,
    ChatTurn,
    HistoryTurn,
    TurnStatus,
)


def bounded_history(turns: Iterable[ChatTurn], *, max_turns: int = HISTORY_MAX_TURNS,
                    max_chars: int = HISTORY_MAX_CHARS) -> tuple[HistoryTurn, ...]:  # fmt: skip
    completed = sorted(
        (t for t in turns if t.status is TurnStatus.COMPLETED and t.assistant_text is not None),
        key=lambda t: t.sequence,
    )
    selected: list[HistoryTurn] = []
    used = 0
    for turn in reversed(completed[-max_turns:] if max_turns > 0 else []):
        answer = turn.assistant_text or ""
        size = len(turn.user_text) + len(answer)
        if used + size > max_chars:
            break
        used += size
        selected.append(HistoryTurn(user_text=turn.user_text, assistant_text=answer))
    return tuple(reversed(selected))
