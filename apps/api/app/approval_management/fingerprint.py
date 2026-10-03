"""The exact-subject fingerprint a human approval is bound to (Task 036).

    raw parameters
      -> trusted ActionHandler.validate()          (frozen, typed)
      -> canonical JSON of the validated input     (never of a raw, mutable mapping)
      -> SHA-256 over (version, action, requester principal, company, store, input)

Only the hex digest is stored. The canonical JSON is built in memory and discarded: raw
parameters, the request body and the validated input are never persisted. Execution with
an approval recomputes the digest from the NEW validated input; any change of action,
requester, company, store or business parameters produces a different digest and the
approval does not apply (a new request is needed). The requester is the exact trusted
PRINCIPAL (actor id AND actor type): the same id under another actor type is another
principal and never matches.
"""

import hashlib
import json

from pydantic import BaseModel

FINGERPRINT_VERSION = "approval-subject-v2"


def subject_fingerprint(
    *,
    action_name: str,
    requester_actor_id: str,
    requester_actor_type: str,
    company_id: str,
    store_id: str | None,
    validated_input: BaseModel,
) -> str:
    if not isinstance(validated_input, BaseModel) or not validated_input.model_config.get("frozen"):
        raise TypeError("an approval binds to a frozen, validated input")
    envelope = {
        "version": FINGERPRINT_VERSION,
        "action_name": action_name,
        "requester_actor_id": requester_actor_id,
        "requester_actor_type": requester_actor_type,
        "company_id": company_id,
        "store_id": store_id,
        "input": validated_input.model_dump(mode="json"),
    }
    text = json.dumps(envelope, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                      allow_nan=False)  # fmt: skip
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
