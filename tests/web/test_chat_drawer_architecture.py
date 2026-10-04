"""Ask Agento (Task 042) web architecture: one global drawer in the shell; plain-text
rendering only; no storage, streaming or polling; the confirmation sends only the proposal
id with one Idempotency-Key; chat state is keyed by the operations epoch (a new key, a
disconnect or a Store change discards it); explicit fixed BFF routes only."""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WEB = ROOT / "apps" / "web"
CHAT = WEB / "components" / "chat" / "ChatDrawer.tsx"
SHELL = WEB / "components" / "shell" / "AppShell.tsx"
CLIENT = WEB / "lib" / "product-api" / "client.ts"
CHAT_ROUTES = WEB / "app" / "api" / "product" / "chat"


def code(path: Path) -> str:
    text = re.sub(r"/\*.*?\*/", "", path.read_text(), flags=re.S)
    return "\n".join(re.sub(r"(^|\s)//.*$", "", line) for line in text.splitlines())


def test_one_global_drawer_in_the_shell() -> None:
    shell = code(SHELL)
    assert "<ChatDrawer open={chatOpen} onClose={closeChat} />" in shell
    assert shell.count("<AskAgentoButton") == 2  # desktop sidebar + mobile bar
    assert {p.name for p in (WEB / "components" / "chat").iterdir()} == {"ChatDrawer.tsx"}
    for page in (WEB / "app").rglob("page.tsx"):
        assert "ChatDrawer" not in page.read_text(), page  # global, never per page


def test_plain_text_rendering_without_storage_streaming_or_polling() -> None:
    source = code(CHAT)
    for forbidden in ("dangerouslySetInnerHTML", "innerHTML", "marked", "remark", "markdown",
                      "eval(", "new Function", "<iframe", "localStorage", "sessionStorage",
                      "indexedDB", "document.cookie", "WebSocket", "EventSource",
                      "setInterval", "setTimeout", "fetch(", "/api/", "product-api/server",
                      "history.pushState", "location.hash"):  # fmt: skip
        assert forbidden not in source, forbidden
    css = (WEB / "app" / "globals.css").read_text()
    assert ".chat-msg__text { margin: 0; white-space: pre-wrap;" in css
    assert "MAX_CHAT_MESSAGE_CHARS = 8000" in source
    assert "maxLength={MAX_CHAT_MESSAGE_CHARS}" in source
    # Enter sends, Shift+Enter is a newline, IME composition is respected.
    assert 'event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing' in source


def test_chat_state_is_keyed_by_the_operations_epoch() -> None:
    source = code(CHAT)
    assert "key={session.operationsEpoch}" in source
    assert "useProductSession()" in source and "setApiKey" not in source


def test_the_confirmation_sends_only_the_proposal_id_and_one_key() -> None:
    client = code(CLIENT)
    confirm = client.split("export function confirmTicketProposal(", 1)[1].split("\n}\n", 1)[0]
    assert "body: JSON.stringify({ proposal_id: proposalId })" in confirm
    assert '"Idempotency-Key": idempotencyKey' in confirm
    for field in ("title", "description", "action", "store_id"):
        assert field not in confirm, field
    turn = client.split("export function sendChatTurn(", 1)[1].split("\n}\n", 1)[0]
    assert "Idempotency-Key" not in turn
    # Only the confirm BFF route forwards an Idempotency-Key.
    forwarding = sorted(str(p.relative_to(CHAT_ROUTES)) for p in CHAT_ROUTES.rglob("route.ts")
                        if "idempotencyKey: true" in code(p))  # fmt: skip
    assert forwarding == ["ticket-proposals/confirm/route.ts"]
    # One key per proposal, reused for retries of the same proposal.
    source = code(CHAT)
    assert "keyRef.current ??= newIdempotencyKey();" in source


def test_ticket_created_is_shown_only_for_a_verified_result() -> None:
    source = code(CHAT)
    assert source.count('"Ticket created"') == 2
    assert 'if (ticket.status === "verified") return ["Ticket created", "success"];' in source
    assert '{checked === "verified" ? "Ticket created" : checked}' in source


def test_only_allowlisted_fixed_details_reach_the_ui() -> None:
    client = code(CLIENT)
    assert "details.includes(body.detail)" in client
    allowlist = client.split("export const CHAT_ERROR_DETAILS = [", 1)[1].split("] as const;", 1)[0]
    assert '"Operations Agent is disabled"' in allowlist
    assert len(re.findall(r'"[^"]+"', allowlist)) == 12
