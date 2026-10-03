"""Product-owned human approval (Task 036).

Governance policy returns REQUIRE_APPROVAL for MEDIUM_RISK / HIGH_RISK actions. This
package makes that a real, durable governance layer: requests (created ONLY by
``ExecutionCoordinator`` through ``ProductApprovalBroker``), human decisions (approve,
reject, cancel; lazy deterministic expiry), the two-person rule, a human-only rule
(an Agent never decides), exact-subject binding (a SHA-256 fingerprint of the validated
input; raw parameters are never stored) and one-time consumption.

An approval is an ADDITIONAL authorization condition: it never replaces identity, a
permission or policy; execution re-evaluates governance before consuming it.

    state / models / fingerprint / errors / contracts      pure domain
    broker      ProductApprovalBroker (the execution ApprovalBroker port)
    actions / permissions / handlers / service             governed human decisions

Imports no Agno: this is Agento's own approval model, not an Agent framework primitive.
"""
