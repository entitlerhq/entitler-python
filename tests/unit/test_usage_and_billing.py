"""Usage, holds and batches (section 5.2), and billing answers (sections 5.4 and 5.5)."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import anyio
import httpx
import pytest

from entitler import (
    APIError,
    AsyncEntitlerServer,
    EntitlerServer,
    Feature,
    NextStep,
    PendingBilling,
    PendingCancel,
    PendingMove,
    PendingUnknown,
    SubscribeStep,
    UnknownSource,
    UsageEventInput,
    UsageRefusedError,
    UsageReplayedError,
    UsageSettlementError,
)
from entitler.testing import answers

from .support import KEY, Transport, ok

CREDITS = Feature.metered("ai_credits")
HELD: dict[str, Any] = {
    "outcome": "held",
    "hold_id": "hld_1",
    "amount": 10,
    "expires_at": "2026-07-01T09:35:00.000Z",
    "as_of": "2026-07-01T09:30:00.000Z",
}


def server(transport: Transport, **options: Any) -> EntitlerServer:
    options.setdefault("max_retries", 0)
    options.setdefault("cache", None)
    return EntitlerServer(KEY, http_client=transport.client(), **options)


def held(**changes: Any) -> dict[str, Any]:
    return ok(answers.usage_result(**{**HELD, **changes}))


def bodies(transport: Transport) -> list[tuple[str, str, Any]]:
    return [
        (r.method, r.url.path, json.loads(r.content) if r.content else None)
        for r in transport.requests
    ]


@pytest.mark.parametrize(
    "outcome",
    [
        "recorded",
        "duplicate",
        "refused",
        "held",
        "settled",
        "released",
        "cancelled",
        "adjusted",
        "novel",
    ],
)
def test_every_usage_outcome_decodes(outcome: str, transport: Transport) -> None:
    transport.answers = [ok(answers.usage_result(outcome=outcome))]
    result = server(transport).customer("u").record_usage(CREDITS, 1, idempotency_key="k")
    assert result.outcome == outcome


def test_start_hold_answers_a_handle_and_finish_settles_the_used_amount(
    transport: Transport,
) -> None:
    transport.answers = [held(), held(outcome="settled")]
    hold = server(transport).customer("u").start_hold(CREDITS, 10, idempotency_key="msg-1")
    assert (hold.id, hold.amount, hold.duplicate) == ("hld_1", 10, False)
    assert hold.expires_at.year == 2026
    assert hold.result.outcome == "held"
    hold.use(4)
    hold.use(6)
    assert hold.finish().outcome == "settled"
    assert hold.finish().outcome == "settled"
    assert hold.release().outcome == "settled"
    assert bodies(transport)[1] == ("POST", "/customers/u/usage/holds/hld_1/settle", {"amount": 6})
    assert len(transport.requests) == 2
    assert "Hold(id='hld_1'" in repr(hold)


def test_finish_settles_the_held_amount_when_nothing_was_reported(transport: Transport) -> None:
    transport.answers = [held(), held(outcome="settled")]
    hold = server(transport).customer("u").start_hold(CREDITS, 10, idempotency_key="k")
    hold.finish()
    assert bodies(transport)[1][2] == {"amount": 10}


def test_use_zero_settles_zero(transport: Transport) -> None:
    transport.answers = [held(), held(outcome="settled")]
    hold = server(transport).customer("u").start_hold(CREDITS, 10, idempotency_key="k")
    hold.use(0)
    hold.finish()
    assert bodies(transport)[1][2] == {"amount": 0}
    with pytest.raises(ValueError, match=r"Pass the amount used as a whole number of 0 or more\."):
        hold.use(-1)


def test_an_excess_is_recorded_in_observe_mode_under_the_excess_key(transport: Transport) -> None:
    transport.answers = [held(), held(outcome="settled"), ok(answers.usage_result())]
    hold = server(transport).customer("u").start_hold(CREDITS, 10, idempotency_key="msg-1")
    hold.use(13)
    hold.finish()
    assert bodies(transport)[1][2] == {"amount": 10}
    assert bodies(transport)[2] == (
        "POST",
        "/customers/u/usage",
        {"feature": "ai_credits", "amount": 3, "mode": "observe"},
    )
    assert transport.requests[2].headers["Idempotency-Key"] == "msg-1:excess"


def test_an_expired_hold_records_the_whole_amount_under_the_excess_key(
    transport: Transport,
) -> None:
    expired = {"status": 409, "body": answers.error("hold_expired")}
    transport.answers = [held(), expired, ok(answers.usage_result())]
    hold = server(transport).customer("u").start_hold(CREDITS, 10, idempotency_key="msg-1")
    hold.use(7)
    assert hold.finish().outcome == "recorded"
    assert bodies(transport)[2][2] == {"feature": "ai_credits", "amount": 7, "mode": "observe"}
    assert transport.requests[2].headers["Idempotency-Key"] == "msg-1:excess"


def test_a_failed_settlement_raises_with_what_is_left_to_do(transport: Transport) -> None:
    transport.answers = [held(), {"status": 500}]
    hold = server(transport).customer("u").start_hold(CREDITS, 10, idempotency_key="k")
    hold.use(12)
    with pytest.raises(UsageSettlementError) as raised:
        hold.finish()
    assert (raised.value.hold_id, raised.value.amount, raised.value.excess) == ("hld_1", 10, 2)
    assert isinstance(raised.value.__cause__, APIError)
    with pytest.raises(UsageSettlementError):
        hold.finish()


def test_release_frees_the_hold(transport: Transport) -> None:
    transport.answers = [held(), held(outcome="released")]
    hold = server(transport).customer("u").start_hold(CREDITS, 10, idempotency_key="k")
    assert hold.release().outcome == "released"
    assert bodies(transport)[1][:2] == ("DELETE", "/customers/u/usage/holds/hld_1")


def test_disposal_settles_what_was_reported_else_releases(transport: Transport) -> None:
    client = server(transport)
    transport.answers = [held(), held(outcome="settled"), held(), held(outcome="released")]
    with client.customer("u").start_hold(CREDITS, 10, idempotency_key="a") as hold:
        hold.use(3)
    with client.customer("u").start_hold(CREDITS, 10, idempotency_key="b"):
        pass
    assert [b[1] for b in bodies(transport)] == [
        "/customers/u/usage/holds",
        "/customers/u/usage/holds/hld_1/settle",
        "/customers/u/usage/holds",
        "/customers/u/usage/holds/hld_1",
    ]


def test_disposal_reports_failures_instead_of_raising(transport: Transport) -> None:
    seen: list[Exception] = []

    def broken(error: Exception) -> None:
        seen.append(error)
        raise RuntimeError("on_error broke")

    client = server(transport, on_error=broken)
    transport.answers = [held(), {"status": 500}, held(), {"status": 500}]
    with client.customer("u").start_hold(CREDITS, 10, idempotency_key="a") as hold:
        hold.use(3)
    with client.customer("u").start_hold(CREDITS, 10, idempotency_key="b"):
        pass
    assert isinstance(seen[0], UsageSettlementError)
    assert isinstance(seen[1], APIError)


class WorkFailedError(Exception):
    """The body's own failure, which disposal must never replace."""


def test_disposal_never_replaces_the_bodys_error(transport: Transport) -> None:
    seen: list[Exception] = []
    client = server(transport, on_error=seen.append)
    transport.answers = [held()]
    hold = client.customer("u").start_hold(CREDITS, 10, idempotency_key="a")

    def work() -> None:
        with hold:
            client.close()
            raise WorkFailedError

    with pytest.raises(WorkFailedError):
        work()
    assert isinstance(seen[0], RuntimeError)


@pytest.mark.anyio
async def test_async_disposal_never_replaces_the_bodys_error() -> None:
    transport = Transport()
    transport.answers = [held()]
    seen: list[Exception] = []
    client = AsyncEntitlerServer(
        KEY, http_client=transport.async_client(), max_retries=0, on_error=seen.append
    )
    hold = await client.customer("u").start_hold(CREDITS, 10, idempotency_key="a")

    async def work() -> None:
        async with hold:
            await client.aclose()
            raise WorkFailedError

    with pytest.raises(WorkFailedError):
        await work()
    assert isinstance(seen[0], RuntimeError)


def test_only_the_first_action_acts(transport: Transport) -> None:
    transport.answers = [held(), held(outcome="released")]
    hold = server(transport).customer("u").start_hold(CREDITS, 10, idempotency_key="k")
    with hold:
        hold.release()
    hold.finish()
    assert len(transport.requests) == 2


def test_start_hold_on_an_open_duplicate_answers_a_handle(transport: Transport) -> None:
    transport.answers = [held(outcome="duplicate")]
    hold = server(transport).customer("u").start_hold(CREDITS, 10, idempotency_key="k")
    assert hold.duplicate is True


@pytest.mark.parametrize(
    "changes",
    [
        {"outcome": "settled"},
        {"outcome": "released"},
        {"outcome": "duplicate", "expires_at": "2026-07-01T09:30:00.000Z"},
        {"outcome": "duplicate", "expires_at": "2026-07-01T09:29:00.000Z"},
        {"outcome": "duplicate", "hold_id": None},
    ],
)
def test_start_hold_raises_on_a_replay_without_reading_the_clock(
    changes: dict[str, Any], transport: Transport
) -> None:
    transport.answers = [held(**changes)]
    with pytest.raises(UsageReplayedError) as raised:
        server(transport).customer("u").start_hold(CREDITS, 10, idempotency_key="k")
    assert raised.value.result.outcome == changes["outcome"]


@pytest.mark.parametrize("refusal", ["over_allowance", "not_entitled"])
def test_start_hold_raises_on_a_refusal(refusal: str, transport: Transport) -> None:
    transport.answers = [held(outcome="refused", refusal=refusal, hold_id=None)]
    with pytest.raises(UsageRefusedError) as raised:
        server(transport).customer("u").start_hold(CREDITS, 10, idempotency_key="k")
    assert raised.value.result.refusal == refusal
    assert "ai_credits" in str(raised.value)


def test_with_hold_finishes_after_the_block(transport: Transport) -> None:
    transport.answers = [held(), held(outcome="settled")]
    with server(transport).customer("u").with_hold(CREDITS, 10, idempotency_key="k") as hold:
        hold.use(4)
    assert bodies(transport)[1][2] == {"amount": 4}


def test_with_hold_settles_the_reported_amount_when_the_block_fails(transport: Transport) -> None:
    transport.answers = [held(), held(outcome="settled")]
    customer = server(transport).customer("u")
    with (  # noqa: PT012 - the block is the work
        pytest.raises(KeyError),
        customer.with_hold(CREDITS, 10, idempotency_key="k") as hold,
    ):
        hold.use(3)
        raise KeyError("work failed")
    assert bodies(transport)[1][2] == {"amount": 3}


def test_with_hold_releases_when_the_block_fails_before_reporting(transport: Transport) -> None:
    transport.answers = [held(), held(outcome="released")]
    with (
        pytest.raises(KeyError),
        server(transport).customer("u").with_hold(CREDITS, 10, idempotency_key="k"),
    ):
        raise KeyError("work failed")
    assert bodies(transport)[1][0] == "DELETE"


def test_with_hold_never_runs_the_block_after_a_refusal(transport: Transport) -> None:
    transport.answers = [held(outcome="refused", refusal="over_allowance")]
    ran: list[int] = []
    with (
        pytest.raises(UsageRefusedError),
        server(transport).customer("u").with_hold(CREDITS, 10, idempotency_key="k"),
    ):
        ran.append(1)
    assert ran == []


def test_with_hold_raises_a_settlement_error_after_the_block(transport: Transport) -> None:
    transport.answers = [held(), {"status": 500}]
    with (
        pytest.raises(UsageSettlementError),
        server(transport).customer("u").with_hold(CREDITS, 10, idempotency_key="k") as hold,
    ):
        hold.use(10)


def test_hold_keys_are_at_most_193_characters(transport: Transport) -> None:
    customer = server(transport).customer("u")
    with pytest.raises(ValueError, match="1 to 193"):
        customer.start_hold(CREDITS, 1, idempotency_key="k" * 194)
    with pytest.raises(TypeError, match="Pass idempotency_key: a key from your own unit of work"):
        customer.record_usage(CREDITS, 1, idempotency_key=None)  # type: ignore[arg-type]  # a missing key, which the SDK must refuse
    assert transport.requests == []


@pytest.mark.anyio
async def test_async_holds_settle_even_when_the_task_is_cancelled() -> None:
    transport = Transport()
    entered = anyio.Event()
    transport.answers = [held(), held(outcome="settled")]
    client = AsyncEntitlerServer(KEY, http_client=transport.async_client(), max_retries=0)
    customer = client.customer("u")

    async def work() -> None:
        async with customer.with_hold(CREDITS, 10, idempotency_key="k") as hold:
            hold.use(4)
            entered.set()
            await anyio.sleep_forever()

    async with anyio.create_task_group() as group:
        group.start_soon(work)
        await entered.wait()
        group.cancel_scope.cancel()
    assert bodies(transport)[1] == ("POST", "/customers/u/usage/holds/hld_1/settle", {"amount": 4})


@pytest.mark.anyio
async def test_async_hold_handles_finish_release_and_dispose() -> None:
    transport = Transport()
    seen: list[Exception] = []
    client = AsyncEntitlerServer(
        KEY, http_client=transport.async_client(), max_retries=0, on_error=seen.append
    )
    customer = client.customer("u")
    transport.answers = [held(), held(outcome="settled")]
    hold = await customer.start_hold(CREDITS, 10, idempotency_key="a")
    assert (hold.id, hold.amount, hold.duplicate, hold.result.outcome) == (
        "hld_1",
        10,
        False,
        "held",
    )
    assert hold.expires_at.year == 2026
    hold.use(2)
    assert (await hold.finish()).outcome == "settled"
    assert (await hold.release()).outcome == "settled"
    transport.answers = [held(), held(outcome="released")]
    async with await customer.start_hold(CREDITS, 10, idempotency_key="b") as second:
        pass
    assert second._state.done
    transport.answers = [held(), {"status": 500}]
    async with await customer.start_hold(CREDITS, 10, idempotency_key="c") as third:
        third.use(1)
    assert isinstance(seen[0], UsageSettlementError)
    transport.answers = [held(), {"status": 500}]
    with pytest.raises(UsageSettlementError):
        async with customer.with_hold(CREDITS, 10, idempotency_key="d"):
            pass
    assert "AsyncHold(id='hld_1'" in repr(hold)


@pytest.mark.anyio
async def test_an_async_hold_that_runs_past_its_timeout_raises() -> None:
    async def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/holds"):
            return httpx.Response(200, json=answers.usage_result(**HELD))
        await anyio.sleep_forever()
        raise AssertionError

    http = httpx.AsyncClient(transport=httpx.MockTransport(handle))
    client = AsyncEntitlerServer(KEY, http_client=http, max_retries=0)
    hold = await client.customer("u").start_hold(CREDITS, 10, idempotency_key="k", timeout=0.05)
    with pytest.raises(UsageSettlementError):
        await hold.finish()
    hold2 = await client.customer("u").start_hold(CREDITS, 10, idempotency_key="k2", timeout=0.05)
    with pytest.raises(Exception, match="did not answer"):
        await hold2.release()


def events(count: int, start: int = 0) -> list[UsageEventInput]:
    return [
        UsageEventInput(
            customer=f"user_{i % 3}", feature=CREDITS, amount=i + 1, idempotency_key=f"evt-{i}"
        )
        for i in range(start, start + count)
    ]


def batch_answer(request: httpx.Request) -> httpx.Response:
    sent = json.loads(request.content)["events"]
    results = [
        {
            "index": i,
            "outcome": "recorded" if i % 2 == 0 else "duplicate",
            "id": f"u{i}",
            "late": False,
            "error": None,
        }
        for i in range(len(sent))
    ]
    return httpx.Response(
        200,
        json=answers.usage_events_result(results=results),
        headers={"Idempotent-Replayed": "true"},
    )


def test_batches_split_at_500_and_answer_in_input_order(transport: Transport) -> None:
    transport.fallback = batch_answer
    result = server(transport).record_usage_batch(events(1001), register=True)
    assert [len(json.loads(r.content)["events"]) for r in transport.requests] == [500, 500, 1]
    assert all(json.loads(r.content)["register"] is True for r in transport.requests)
    assert [r.index for r in result.results] == list(range(1001))
    assert result.results[600].idempotency_key == "evt-600"
    assert result.results[0].replayed is True
    assert (result.recorded, result.duplicates, result.errors) == (501, 500, 0)


def test_a_batch_request_that_fails_answers_its_events_error(transport: Transport) -> None:
    transport.answers = [
        {"failure": "connection"},
        {"failure": "timeout"},
        {"status": 422, "body": answers.error("idempotency_mismatch", "Mismatch.")},
    ]
    transport.fallback = batch_answer
    result = server(transport).record_usage_batch(events(1600))
    codes = [r.error.code if r.error else None for r in result.results]
    assert codes[0] == "connection_failed"
    assert codes[500] == "timed_out"
    assert codes[1000] == "idempotency_mismatch"
    assert codes[1500] is None
    assert result.results[0].error is not None
    assert result.results[0].error.message == "Entitler could not be reached."
    assert result.errors == 1500


@pytest.mark.parametrize(
    "results",
    [
        [],
        [{"index": 0}, {"index": 0}],
        [{"index": 0}, {"index": 5}],
        [{"index": 0}, {"index": 1}, {"index": 2}],
    ],
)
def test_a_batch_answer_not_covering_each_event_once_is_invalid(
    results: list[dict[str, Any]], transport: Transport
) -> None:
    full = [{"outcome": "recorded", "id": None, "late": False, "error": None, **r} for r in results]
    transport.answers = [ok(answers.usage_events_result(results=full))]
    result = server(transport).record_usage_batch(events(2))
    assert [r.error.code if r.error else None for r in result.results] == ["invalid_response"] * 2


def test_a_batch_with_nothing_to_send_sends_nothing(transport: Transport) -> None:
    bad = [UsageEventInput(customer="", feature=CREDITS, amount=1, idempotency_key="k")]
    result = server(transport).record_usage_batch(bad)
    assert transport.requests == []
    assert result.results[0].error is not None
    assert result.results[0].error.code == "invalid_body"
    assert result.results[0].idempotency_key == "k"


STEPS: list[tuple[dict[str, Any], str, Callable[[SubscribeStep], bool]]] = [
    (answers.subscribe_step(), NextStep.DONE, lambda s: s.change is not None and s.change.changed),
    (
        answers.subscribe_step(next="pay"),
        NextStep.PAY,
        lambda s: s.url == "https://checkout.example.com/pay",
    ),
    (
        answers.subscribe_step(next="confirming"),
        NextStep.CONFIRMING,
        lambda s: s.change is None,
    ),
    (
        answers.subscribe_step(next="manage", billed_by="google"),
        NextStep.MANAGE,
        lambda s: s.billed_by == "google",
    ),
    (answers.subscribe_step(next="wire_transfer"), "wire_transfer", lambda s: s.url is None),
]


@pytest.mark.parametrize(
    ("body", "next_step", "check"),
    STEPS,
)
def test_every_subscribe_step(
    body: dict[str, Any], next_step: str, check: Any, transport: Transport
) -> None:
    transport.answers = [ok(body)]
    step = server(transport).customer("u").subscribe("pro")
    assert step.next == next_step
    assert check(step)


def test_a_declined_payment_is_an_api_error(transport: Transport) -> None:
    payment = {"status": "declined", "url": None}
    transport.answers = [
        {"status": 402, "body": answers.error("payment_required", "Declined.", payment=payment)}
    ]
    with pytest.raises(APIError) as raised:
        server(transport).customer("u").subscribe("pro")
    assert raised.value.payment is not None
    assert raised.value.payment.status == "declined"


def test_a_plan_change_with_nothing_changed(transport: Transport) -> None:
    transport.answers = [ok(answers.plan_change(changed=False))]
    assert server(transport).customer("u").cancel().changed is False


@pytest.mark.parametrize(
    ("pending", "kind"),
    [
        ({"type": "move", "plan": {"id": "p", "key": "pro", "name": "Pro"}}, PendingMove),
        ({"type": "cancel", "movingTo": None}, PendingCancel),
        ({"type": "billing", "billing": "outside"}, PendingBilling),
        ({"type": "pause"}, PendingUnknown),
    ],
)
def test_every_pending_change(pending: dict[str, Any], kind: type, transport: Transport) -> None:
    body = answers.customer_plans(held=[answers.held_plan(pending=pending)])
    transport.answers = [ok(body)]
    plans = server(transport).customer("u").plans()
    assert isinstance(plans.held[0].pending, kind)


def test_sources_of_every_kind_decode(transport: Transport) -> None:
    sources: list[dict[str, Any]] = [
        {
            "type": "plan",
            "plan": "pro",
            "name": "Pro",
            "version": 1,
            "byDefault": False,
            "value": True,
        },
        {"type": "addon", "plan": "a", "name": "A", "version": 1, "quantity": 2, "value": 5},
        {"type": "grant", "grant": "g", "until": None, "value": "unlimited"},
        {"type": "banked", "value": -3},
        {"type": "group", "features": ["x"]},
        {"type": "promotion"},
    ]
    transport.answers = [ok(answers.check(sources=sources))]
    check = server(transport).customer("u").check("sso")
    assert [s.type for s in check.sources] == [
        "plan",
        "addon",
        "grant",
        "banked",
        "group",
        "promotion",
    ]
    assert isinstance(check.sources[-1], UnknownSource)


def test_grant_values_decode_from_their_strings(transport: Transport) -> None:
    client = server(transport)
    for raw, value in (("", True), ("unlimited", "unlimited"), ("42", 42)):
        transport.answers = [ok(answers.grant_change(grant_body=answers.grant(value=raw)))]
        assert client.customer("u").grant("sso").grant.value == value
    transport.answers = [ok(answers.grant_change(grant_body=answers.grant(value="lots")))]
    with pytest.raises(APIError) as raised:
        client.customer("u").grant("sso")
    assert raised.value.code == "invalid_response"
