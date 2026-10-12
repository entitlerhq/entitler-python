"""Live API tests against https://api.entitler.dev, with ENTITLER_TEST_KEY.

They write no catalogue, track or key, and every customer they create is named
`sdk-python-<random>` and erased at the end, even when a test fails, so runs can overlap.

The SDK test project has no sign-in provider and no Stripe connection, so the identity client
and the `pay`, `confirming` and `manage` steps are covered by the unit tests only.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest

from entitler import (
    APIError,
    AsyncEntitlerServer,
    CustomerTokenScope,
    EntitlerClient,
    EntitlerServer,
    ErrorCode,
    Feature,
    MeteredCheck,
    NextStep,
    PlanBilling,
    ServerCustomer,
    UsageEventInput,
    UsageMode,
    UsageReplayedError,
    verify_snapshot,
)

KEY = os.environ.get("ENTITLER_TEST_KEY", "")
PUBLISHABLE = os.environ.get("ENTITLER_TEST_PUBLISHABLE_KEY", "")
CREDITS = Feature.metered("ai_credits")
EXPORT_PDF = Feature.boolean("export_pdf")
COLLABORATION = Feature.group("collaboration", ("team_seats", "shared_folders"))

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not KEY, reason="ENTITLER_TEST_KEY is not set, so the live API tests did not run."
    ),
]


def unique(label: str = "") -> str:
    return f"sdk-python-{label}{uuid.uuid4().hex[:12]}"


@pytest.fixture(scope="module")
def server() -> Iterator[EntitlerServer]:
    with EntitlerServer(KEY, cache=None) as client:
        yield client


@pytest.fixture
def customer(server: EntitlerServer) -> Iterator[ServerCustomer]:
    found = server.customer(unique())
    found.register(name="SDK test", email="sdk-python@example.com")
    try:
        yield found
    finally:
        found.erase()


def allowance(customer: ServerCustomer) -> int:
    check = customer.check(CREDITS)
    assert isinstance(check, MeteredCheck)
    assert isinstance(check.value, int)
    return check.value


def test_scopes_pricing_and_features(server: EntitlerServer) -> None:
    held = {scope.value for scope in server.scopes().scopes}
    assert {"plans:read", "entitlements:read", "usage:write", "tracks:assign"} <= held
    pricing = server.pricing(visitor=server.new_visitor_id())
    assert {"free", "pro", "pro_basic"} <= {plan.key for plan in pricing.plans}
    pro = next(plan for plan in pricing.plans if plan.key == "pro")
    assert {(period.key, period.label) for period in pro.periods} == {
        ("monthly", "Monthly"),
        ("yearly", "Yearly"),
    }
    features = {feature.key: feature for feature in server.features().features}
    assert features["ai_credits"].type == "metered"
    assert features["collaboration"].type == "group"
    assert set(features["collaboration"].includes) == {"team_seats", "shared_folders"}
    assert features["support_contacts"].type == "config"


def test_register_details_and_update(server: EntitlerServer) -> None:
    customer = server.customer(unique())
    try:
        first = customer.register(name="Ada", email="ada@example.com", metadata={"team": "a"})
        assert first.created
        second = customer.register(name="Ada Lovelace", metadata={"team": None, "plan": "b"})
        assert not second.created
        details = customer.details()
        assert details.customer.name == "Ada Lovelace"
        assert details.customer.metadata == {"plan": "b"}
        updated = customer.update(email="ada@lovelace.example.com")
        assert updated.email == "ada@lovelace.example.com"
        assert updated.replayed is False
    finally:
        customer.erase()
        customer.erase()


def test_checks_entitlements_and_revalidation(server: EntitlerServer) -> None:
    with EntitlerServer(KEY) as cached:
        customer = cached.customer(unique())
        customer.register(name="Cache test")
        try:
            assert not customer.check(EXPORT_PDF).entitled
            assert not customer.check(EXPORT_PDF).entitled
            entitlements = customer.entitlements()
            assert customer.entitlements().customer == entitlements.customer
            assert not entitlements.has(COLLABORATION)
            assert entitlements.get(COLLABORATION) is not None
            server.customer(customer.id).set_plan("pro", period="monthly", reason="SDK test")
            assert customer.check(EXPORT_PDF, revalidate=True).entitled
        finally:
            customer.erase()


def test_usage_gate_observe_and_replays(customer: ServerCustomer) -> None:
    limit = allowance(customer)
    recorded = customer.record_usage(CREDITS, 1, idempotency_key=unique("use-"))
    assert recorded.outcome == "recorded"
    key = unique("replay-")
    customer.record_usage(CREDITS, 1, idempotency_key=key)
    replay = customer.record_usage(CREDITS, 1, idempotency_key=key)
    assert (replay.outcome, replay.replayed) == ("duplicate", True)
    refused = customer.record_usage(CREDITS, limit, idempotency_key=unique("refused-"))
    assert (refused.outcome, refused.refusal) == ("refused", "over_allowance")
    over = customer.record_usage(
        CREDITS, limit, idempotency_key=unique("observe-"), mode=UsageMode.OBSERVE
    )
    assert over.outcome == "recorded"
    assert over.over_by > 0
    registered = customer.details().customer.created_at
    dated = customer.record_usage(
        CREDITS,
        1,
        idempotency_key=unique("dated-"),
        mode=UsageMode.OBSERVE,
        occurred_at=registered + timedelta(milliseconds=500),
    )
    assert dated.occurred_at is not None
    log = list(customer.usage().log)
    assert len(log) >= 4
    cancelled = customer.cancel_usage(recorded.id or "")
    assert (cancelled.outcome, cancelled.meter_change) == ("cancelled", -1)
    by = customer.adjust_meter(CREDITS, by=-1, idempotency_key=unique("by-"))
    assert by.outcome == "adjusted"
    to = customer.adjust_meter(CREDITS, to=0, idempotency_key=unique("to-"))
    assert (to.outcome, to.used) == ("adjusted", 0)


def test_holds(customer: ServerCustomer) -> None:
    key = unique("hold-")
    hold = customer.start_hold(CREDITS, 5, idempotency_key=key)
    hold.use(3)
    assert hold.finish().outcome == "settled"
    with pytest.raises(UsageReplayedError):
        customer.start_hold(CREDITS, 5, idempotency_key=key)
    released = customer.start_hold(CREDITS, 5, idempotency_key=unique("release-"))
    assert released.release().outcome == "released"
    with customer.with_hold(CREDITS, 4, idempotency_key=unique("with-")) as work:
        work.use(2)
    with pytest.raises(RuntimeError):
        fail_after_using(customer, 1)
    with pytest.raises(RuntimeError):
        fail_after_using(customer, None)
    assert customer.check(CREDITS).used == 3 + 2 + 1


def fail_after_using(customer: ServerCustomer, used: int | None) -> None:
    with customer.with_hold(CREDITS, 4, idempotency_key=unique("fail-")) as hold:
        if used is not None:
            hold.use(used)
        raise RuntimeError("the work failed")


def test_batches_replay_when_resent(server: EntitlerServer, customer: ServerCustomer) -> None:
    key = unique("batch-")
    customer.record_usage(CREDITS, 1, idempotency_key=f"{key}-0", mode=UsageMode.OBSERVE)
    events = [
        UsageEventInput(
            customer=customer.id, feature=CREDITS, amount=1, idempotency_key=f"{key}-{i}"
        )
        for i in range(3)
    ]
    first = server.record_usage_batch(events)
    assert [r.outcome for r in first.results] == ["duplicate", "recorded", "recorded"]
    again = server.record_usage_batch(events)
    assert all(r.replayed for r in again.results)


def test_customer_plans_and_pricing(customer: ServerCustomer) -> None:
    plans = customer.plans()
    held = plans.held[0]
    assert held.plan.key == "free"
    assert held.billed_by is None
    pro = next(option for option in plans.options if option.plan.key == "pro")
    assert pro.action == "buy"
    assert [period.key for period in pro.periods] == ["monthly", "yearly"]
    assert customer.pricing().customer == customer.id


def test_customer_tokens_and_the_in_app_client(
    server: EntitlerServer, customer: ServerCustomer
) -> None:
    plain = customer.token()
    assert [str(scope) for scope in plain.scopes] == ["entitlements:read"]
    scopes = [
        CustomerTokenScope.ENTITLEMENTS_READ,
        CustomerTokenScope.USAGE_WRITE,
        CustomerTokenScope.BILLING_SELF,
    ]
    with EntitlerClient(token=lambda: customer.token(scopes=scopes).token) as client:
        assert {str(s) for s in client.scopes().scopes} == {
            "entitlements:read",
            "usage:write",
            "billing:self",
        }
        assert not client.me.check(EXPORT_PDF).entitled
        assert client.me.id == customer.id
        assert (
            client.me.record_usage(CREDITS, 1, idempotency_key=unique("app-")).outcome == "recorded"
        )
        try:
            outcome: str = client.me.subscribe("pro_basic", period="monthly").next
        except APIError as error:
            outcome = error.code
        assert outcome in {NextStep.DONE, ErrorCode.CAPABILITY_REQUIRED}
    with EntitlerClient(token=plain.token) as limited, pytest.raises(APIError) as refused:
        limited.me.subscribe("pro", period="monthly")
    assert (refused.value.status, refused.value.code) == (403, "scope_required")


@pytest.mark.skipif(
    not PUBLISHABLE,
    reason="ENTITLER_TEST_PUBLISHABLE_KEY is not set, so signed-out pricing did not run.",
)
def test_signed_out_pricing_with_a_publishable_key() -> None:
    with EntitlerClient(key=PUBLISHABLE) as client:
        assert {"free", "pro"} <= {plan.key for plan in client.pricing().plans}


def test_snapshots_verify_offline(server: EntitlerServer, customer: ServerCustomer) -> None:
    issued = customer.snapshot()
    keys = server.snapshot_keys()
    environment = customer.check(EXPORT_PDF).environment.id
    found = verify_snapshot(issued.token, keys=keys, customer=customer.id, environment=environment)
    assert found.customer == customer.id
    assert not found.entitlements.has(EXPORT_PDF)


def test_tracks(customer: ServerCustomer) -> None:
    assert customer.set_track("Beta").track.name == "Beta"
    assert customer.set_track(None).track.name == "All customers"


def test_as_of_reads(customer: ServerCustomer) -> None:
    later = datetime.now(UTC) + timedelta(days=40)
    try:
        outcome = customer.check(EXPORT_PDF, as_of=later).as_of.date().isoformat()
    except APIError as error:
        outcome = error.code
    assert outcome in {later.date().isoformat(), ErrorCode.CAPABILITY_REQUIRED}


def test_billing_the_customer_chooses(customer: ServerCustomer) -> None:
    step = customer.subscribe("pro", period="monthly", idempotency_key=unique("sub-"))
    assert step.next == NextStep.DONE
    assert step.change is not None
    assert step.change.plan is not None
    assert step.change.plan.key == "pro"
    assert customer.plans().held[0].period is not None
    add_on = customer.subscribe("sso_addon")
    assert add_on.change is not None
    assert add_on.change.quantity == 1
    assert customer.cancel(add_on="sso_addon").quantity == 0
    assert customer.cancel().effective == "renewal"
    assert customer.undo_pending_change().changed
    assert not customer.sync_billing().changed
    with pytest.raises(APIError) as stale:
        customer.billing_portal(return_url="https://app.example.com/billing")
    assert (stale.value.status, stale.value.code) == (409, "stale")


def test_cancel_with_nothing_to_cancel(customer: ServerCustomer) -> None:
    assert customer.cancel().changed is False


def test_company_decisions(customer: ServerCustomer) -> None:
    period = next(o for o in customer.plans().options if o.plan.key == "pro_basic").periods[0].key
    moved = customer.set_plan("pro_basic", period=period, actor="sdk-test", reason="Testing")
    assert moved.changed
    assert customer.set_plan("pro_basic", period=period).changed is False
    assert customer.set_add_on("support_standard", quantity=1).quantity == 1
    assert customer.set_add_on("support_standard", quantity=0).quantity == 0
    until = datetime.now(UTC) + timedelta(days=30)
    timed = customer.set_plan("pro", period="monthly", until=until, billing=PlanBilling.END)
    assert timed.until is not None
    grant = customer.grant("sso", days=7, reason="Support ticket", actor="agent_7").grant
    assert grant.feature == "sso"
    assert customer.revoke_grant(grant.id).grant.revoked_at is not None


def test_customer_list_and_errors(server: EntitlerServer, customer: ServerCustomer) -> None:
    found = [c.external_id for c in server.customers.list(q=customer.id)]
    assert customer.id in found
    page = next(iter(server.customers.list(include_test=True).pages()))
    if page.next is not None:
        assert next(iter(server.customers.list(cursor=page.next).pages())).items is not None
    with pytest.raises(APIError) as missing:
        server.customer(unique("nobody-")).record_usage(CREDITS, 1, idempotency_key=unique())
    assert missing.value.code == "customer_not_found"
    assert missing.value.request_id
    with pytest.raises(APIError) as unknown:
        customer.check("no_such_feature")
    assert unknown.value.code == "feature_not_found"
    assert unknown.value.request_id


@pytest.mark.anyio
@pytest.mark.parametrize("anyio_backend", ["asyncio", "trio"])
async def test_the_async_client_reads_and_writes() -> None:
    async with AsyncEntitlerServer(KEY) as client:
        customer = client.customer(unique("async-"))
        await customer.register(name="Async")
        try:
            assert not await customer.is_entitled(EXPORT_PDF, default=True)
            async with customer.with_hold(
                CREDITS, 2, idempotency_key=unique("async-hold-")
            ) as hold:
                hold.use(1)
            assert (await customer.check(CREDITS)).used == 1
        finally:
            await customer.erase()
