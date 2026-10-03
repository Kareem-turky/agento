"""ONE Product observability per deployed application (Task 034).

The deployment factory chooses the Product observability once and hands the SAME
instance to the business composition (the Workflow engine) and to ``create_app``. Through
the normal composed Product path (real Product API-key auth, mock backend, migrated
PostgreSQL), executing ``operations.daily_report`` must reach that one observer with the
HTTP, daily-report, Workflow-run and Step-attempt observations of the same request, and
no second default Product observability may be built.
"""

import pytest
from fastapi.testclient import TestClient

import app.bootstrap as bootstrap
import app.observability.otel as otel
from app.bootstrap import create_deployment_app
from app.integrations.commerce.mock import EntityType, canonical_id
from app.observability import ObservabilityRuntime, ProductOperation
from tests.support.observability import RecordingObservability
from tests.support.product_auth import deployment_settings, principal
from tests.support.scripted_tool_model import ScriptedToolModel

pytestmark = pytest.mark.integration

COMPANY = str(canonical_id(EntityType.COMPANY, "acct_demo"))
SOUTH = str(canonical_id(EntityType.STORE, "shop_south"))
KEY = "test-one-observer-key-" + "o" * 24
PATH = "/api/v1/operations/reports/daily"
WORKFLOW = (ProductOperation.WORKFLOW_RUN, ProductOperation.WORKFLOW_STEP_ATTEMPT)


@pytest.fixture
def no_second_default(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Detects (never replaces) any other Product observability being constructed."""
    built: list[str] = []
    original = otel.OpenTelemetryObservability.__init__

    def tracking(self, *args, **kwargs):
        built.append("OpenTelemetryObservability")
        original(self, *args, **kwargs)

    monkeypatch.setattr(otel.OpenTelemetryObservability, "__init__", tracking)
    return built


def settings_for(settings):
    reader = principal(KEY, key_id="one-observer", actor_id="one-observer-actor",
                       permissions=frozenset({"stores.read", "orders.read", "shipments.read"}),
                       store_ids=frozenset({SOUTH}))  # fmt: skip
    return deployment_settings(settings, "test", company_id=COMPANY, business_backend="mock",
                               product_api_keys=(reader,))  # fmt: skip


def report(app) -> str:
    with TestClient(app) as client:
        answer = client.get(PATH, params={"store_id": SOUTH, "business_date": "2026-03-03"},
                            headers={"Authorization": f"Bearer {KEY}"})  # fmt: skip
        assert answer.status_code == 200, answer.text
        return answer.json()["request_id"]


def test_an_injected_observer_sees_product_and_workflow_observations(
    settings, runtime_settings, migrated, no_second_default
) -> None:
    observer = RecordingObservability()
    model = ScriptedToolModel()
    app = create_deployment_app(settings_for(settings), runtime_settings, model=model,
                                observability=observer)  # fmt: skip
    request_id = report(app)

    # The SAME observer received the Workflow run and its Step attempt ...
    (run,) = observer.of(ProductOperation.WORKFLOW_RUN)
    (attempt,) = observer.of(ProductOperation.WORKFLOW_STEP_ATTEMPT)
    assert run.attributes == {"workflow.id": "operations.daily_report",
                              "workflow.status": "succeeded"}  # fmt: skip
    assert attempt.attributes == {
        "workflow.id": "operations.daily_report", "workflow.step_id": "compute_daily_report",
        "workflow.status": "succeeded", "workflow.retry": False,
    }  # fmt: skip
    # ... and the normal Product observations of the very same request.
    (http,) = [r for r in observer.of(ProductOperation.HTTP_REQUEST)
               if r.attributes.get("http.route") == PATH]  # fmt: skip
    (daily,) = observer.of(ProductOperation.DAILY_REPORT)
    assert {str(r.request_id) for r in (http, daily, run, attempt)} == {request_id}
    assert http.attributes["http.status_code"] == 200
    # No second default Product observability was built for this application.
    assert no_second_default == []
    assert model.requests == []  # and the Workflow made no model call


def test_the_default_observability_is_chosen_once_and_shared(
    settings, runtime_settings, migrated, monkeypatch: pytest.MonkeyPatch
) -> None:
    chosen: list[RecordingObservability] = []

    def one_default(_settings) -> ObservabilityRuntime:
        chosen.append(RecordingObservability())
        return ObservabilityRuntime(chosen[-1], "disabled")  # type: ignore[arg-type]

    # Observe which instance the factory's own default choice produced (detection only:
    # the factory still decides when and how often to choose). Task 039: the default is
    # the deployment observability runtime.
    monkeypatch.setattr(bootstrap, "build_deployment_observability", one_default)
    request_id = report(create_deployment_app(settings_for(settings), runtime_settings,
                                              model=ScriptedToolModel()))  # fmt: skip
    (observer,) = chosen  # chosen exactly once for the whole application
    for operation in (*WORKFLOW, ProductOperation.DAILY_REPORT):
        (record,) = observer.of(operation)
        assert str(record.request_id) == request_id
    assert observer.of(ProductOperation.HTTP_REQUEST)
