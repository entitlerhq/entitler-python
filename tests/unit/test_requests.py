"""Every method's request: method, path, headers and body, and decoding of every answer."""

from __future__ import annotations

import json
import platform
from collections.abc import Callable
from datetime import UTC, datetime, timedelta, timezone
from typing import Any

import httpx
import pytest

from entitler import (
    APIError,
    BooleanCheck,
    CustomerTokenScope,
    EntitlerClient,
    EntitlerServer,
    Feature,
    MeteredCheck,
    PlanBilling,
    Sku,
    UsageMode,
    When,
    __version__,
)
from entitler.testing import answers

from .support import KEY, Transport, ok, wired

CREDITS = Feature.metered("ai_credits")
LATER = datetime(2026, 7, 1, 19, 30, tzinfo=timezone(timedelta(hours=10)))


def make(transport: Transport, **options: Any) -> EntitlerServer:
    wired(transport)
    options.setdefault("cache", None)
    options.setdefault("max_retries", 0)
    return EntitlerServer(KEY, http_client=transport.client(), **options)


CALLS: list[tuple[str, Callable[[EntitlerServer], Any], str, str, Any]] = [
    ("check", lambda s: s.customer("u").check("export_pdf"), "GET", "/customers/u/entitlements/export_pdf", None),
    ("entitlements", lambda s: s.customer("u").entitlements(), "GET", "/customers/u/entitlements", None),
    ("plans", lambda s: s.customer("u").plans(), "GET", "/customers/u/plans", None),
    ("pricing", lambda s: s.customer("u").pricing(), "GET", "/customers/u/pricing", None),
    ("usage", lambda s: s.customer("u").usage(cursor="c1"), "GET", "/customers/u/usage?cursor=c1", None),
    ("details", lambda s: s.customer("u").details(), "GET", "/customers/u", None),
    ("billing", lambda s: s.customer("u").billing(), "GET", "/customers/u/billing", None),
    (
        "record_usage",
        lambda s: s.customer("u").record_usage(
            CREDITS, 3, idempotency_key="k", mode=UsageMode.OBSERVE, occurred_at=LATER, register=True
        ),
        "POST",
        "/customers/u/usage",
        {
            "feature": "ai_credits",
            "amount": 3,
            "mode": "observe",
            "occurredAt": "2026-07-01T09:30:00.000Z",
            "register": True,
        },
    ),
    (
        "record_usage_minimal",
        lambda s: s.customer("u").record_usage(CREDITS, 1, idempotency_key="k"),
        "POST",
        "/customers/u/usage",
        {"feature": "ai_credits", "amount": 1},
    ),
    (
        "hold_usage",
        lambda s: s.customer("u").hold_usage(CREDITS, 5, idempotency_key="k", ttl_seconds=60),
        "POST",
        "/customers/u/usage/holds",
        {"feature": "ai_credits", "amount": 5, "ttlSeconds": 60},
    ),
    (
        "settle_usage",
        lambda s: s.customer("u").settle_usage("hld_1", 0),
        "POST",
        "/customers/u/usage/holds/hld_1/settle",
        {"amount": 0},
    ),
    ("release_usage", lambda s: s.customer("u").release_usage("hld_1"), "DELETE", "/customers/u/usage/holds/hld_1", None),
    ("snapshot", lambda s: s.customer("u").snapshot(), "POST", "/customers/u/snapshots", {}),
    ("snapshot_ttl", lambda s: s.customer("u").snapshot(ttl_seconds=600), "POST", "/customers/u/snapshots", {"ttlSeconds": 600}),
    (
        "subscribe",
        lambda s: s.customer("u").subscribe(
            "pro", period="monthly", quantity=2, return_url="https://app.example.com/back", register=True
        ),
        "POST",
        "/customers/u/subscription",
        {"plan": "pro", "period": "monthly", "quantity": 2, "returnUrl": "https://app.example.com/back", "register": True},
    ),
    ("subscribe_minimal", lambda s: s.customer("u").subscribe("pro"), "POST", "/customers/u/subscription", {"plan": "pro"}),
    ("cancel", lambda s: s.customer("u").cancel(), "DELETE", "/customers/u/subscription", None),
    ("cancel_product", lambda s: s.customer("u").cancel(product="app"), "DELETE", "/customers/u/subscription?product=app", None),
    ("cancel_add_on", lambda s: s.customer("u").cancel(add_on="sso_addon"), "DELETE", "/customers/u/subscription/add-ons/sso_addon", None),
    ("undo", lambda s: s.customer("u").undo_pending_change(), "DELETE", "/customers/u/subscription/pending", None),
    (
        "undo_add_on",
        lambda s: s.customer("u").undo_pending_change(add_on="sso_addon"),
        "DELETE",
        "/customers/u/subscription/add-ons/sso_addon/pending",
        None,
    ),
    (
        "billing_portal",
        lambda s: s.customer("u").billing_portal(return_url="https://app.example.com"),
        "POST",
        "/customers/u/billing-portal",
        {"returnUrl": "https://app.example.com"},
    ),
    ("sync_billing", lambda s: s.customer("u").sync_billing(), "POST", "/customers/u/billing/sync", None),
    ("register_none", lambda s: s.customer("u").register(), "PUT", "/customers/u", None),
    (
        "register",
        lambda s: s.customer("u").register(name="Ada", email="ada@example.com", metadata={"team": "a", "old": None}),
        "PUT",
        "/customers/u",
        {"name": "Ada", "email": "ada@example.com", "metadata": {"team": "a", "old": None}},
    ),
    (
        "update",
        lambda s: s.customer("u").update(metadata={"gone": None}),
        "PATCH",
        "/customers/u",
        {"metadata": {"gone": None}},
    ),
    ("erase", lambda s: s.customer("u").erase(), "DELETE", "/customers/u?erase=true", None),
    ("token", lambda s: s.customer("u").token(), "POST", "/customers/u/tokens", {}),
    (
        "token_scopes",
        lambda s: s.customer("u").token(
            scopes=[CustomerTokenScope.USAGE_WRITE, CustomerTokenScope.BILLING_SELF], ttl_seconds=900
        ),
        "POST",
        "/customers/u/tokens",
        {"scopes": ["usage:write", "billing:self"], "ttlSeconds": 900},
    ),
    ("set_track", lambda s: s.customer("u").set_track("Beta"), "PUT", "/customers/u/track", {"track": "Beta"}),
    ("set_track_none", lambda s: s.customer("u").set_track(None), "PUT", "/customers/u/track", {"track": None}),
    (
        "set_plan",
        lambda s: s.customer("u").set_plan(
            "enterprise",
            period="yearly",
            when=When.END,
            billing=PlanBilling.END,
            until=LATER,
            version="latest",
            register=True,
            reason="Deal won",
            actor="hubspot",
        ),
        "PUT",
        "/customers/u/plan",
        {
            "plan": "enterprise",
            "period": "yearly",
            "when": "end",
            "billing": "end",
            "until": "2026-07-01T09:30:00.000Z",
            "version": "latest",
            "register": True,
            "reason": "Deal won",
            "actor": "hubspot",
        },
    ),
    (
        "set_plan_sku",
        lambda s: s.customer("u").set_plan(Sku(connector="apple", ids={"productId": "pro_m"}), until=LATER),
        "PUT",
        "/customers/u/plan",
        {"sku": {"connector": "apple", "ids": {"productId": "pro_m"}}, "until": "2026-07-01T09:30:00.000Z"},
    ),
    (
        "set_plan_none",
        lambda s: s.customer("u").set_plan(None, product="app", when=When.NOW),
        "PUT",
        "/customers/u/plan",
        {"plan": None, "product": "app", "when": "now"},
    ),
    (
        "set_add_on",
        lambda s: s.customer("u").set_add_on("seats", quantity=0),
        "PUT",
        "/customers/u/add-ons/seats",
        {"quantity": 0},
    ),
    (
        "set_add_on_replaces",
        lambda s: s.customer("u").set_add_on(
            "support_premium",
            quantity=1,
            replaces="support_standard",
            sku=Sku(connector="google", ids={"productId": "sp"}),
            reason="Upgrade",
            actor="agent_7",
        ),
        "PUT",
        "/customers/u/add-ons/support_premium",
        {
            "quantity": 1,
            "sku": {"connector": "google", "ids": {"productId": "sp"}},
            "replaces": "support_standard",
            "reason": "Upgrade",
            "actor": "agent_7",
        },
    ),
    (
        "grant",
        lambda s: s.customer("u").grant("sso", days=30, reason="Ticket 4", actor="agent_7"),
        "POST",
        "/customers/u/grants",
        {"feature": "sso", "days": 30, "reason": "Ticket 4", "actor": "agent_7"},
    ),
    (
        "grant_value",
        lambda s: s.customer("u").grant("team_seats", value=50),
        "POST",
        "/customers/u/grants",
        {"feature": "team_seats", "value": "50"},
    ),
    (
        "grant_unlimited",
        lambda s: s.customer("u").grant("team_seats", value="unlimited"),
        "POST",
        "/customers/u/grants",
        {"feature": "team_seats", "value": "unlimited"},
    ),
    ("revoke_grant", lambda s: s.customer("u").revoke_grant("grt_1"), "DELETE", "/customers/u/grants/grt_1", None),
    (
        "revoke_grant_who",
        lambda s: s.customer("u").revoke_grant("grt_1", reason="Done", actor="agent_7"),
        "DELETE",
        "/customers/u/grants/grt_1",
        {"reason": "Done", "actor": "agent_7"},
    ),
    (
        "adjust_by",
        lambda s: s.customer("u").adjust_meter(CREDITS, by=-500, idempotency_key="k"),
        "POST",
        "/customers/u/meters/ai_credits/adjustments",
        {"by": -500},
    ),
    (
        "adjust_to",
        lambda s: s.customer("u").adjust_meter(CREDITS, to=0, idempotency_key="k", reason="Reset"),
        "POST",
        "/customers/u/meters/ai_credits/adjustments",
        {"to": 0, "reason": "Reset"},
    ),
    ("cancel_usage", lambda s: s.customer("u").cancel_usage("use_1"), "DELETE", "/customers/u/usage/use_1", None),
    ("pricing_server", lambda s: s.pricing(), "GET", "/pricing", None),
    ("features", lambda s: s.features(), "GET", "/pricing/features", None),
    ("scopes", lambda s: s.scopes(), "GET", "/keys/self", None),
    ("snapshot_keys", lambda s: s.snapshot_keys(), "GET", "/customers/snapshot-keys", None),
    (
        "list",
        lambda s: next(iter(s.customers.list(q="acme", cohort="c", track="Beta", include_test=True).pages())),
        "GET",
        "/customers?q=acme&cohort=c&track=Beta&includeTest=true",
        None,
    ),
    ("list_plain", lambda s: list(s.customers.list(include_test=False)), "GET", "/customers", None),
    (
        "create",
        lambda s: s.customers.create("u", name="Ada", email="a@example.com", plan="pro", period="monthly", metadata={"a": "b"}),
        "POST",
        "/customers",
        {"externalId": "u", "name": "Ada", "email": "a@example.com", "plan": "pro", "period": "monthly", "metadata": {"a": "b"}},
    ),
]


@pytest.mark.parametrize(("name", "call", "method", "path", "body"), CALLS, ids=[c[0] for c in CALLS])
def test_each_request(
    name: str, call: Callable[[EntitlerServer], Any], method: str, path: str, body: Any, transport: Transport
) -> None:
    server = make(transport)
    call(server)
    request = transport.requests[0]
    assert request.method == method
    assert str(request.url) == f"https://api.entitler.dev{path}"
    assert request.headers["Accept"] == "application/json"
    assert request.headers["User-Agent"] == f"entitler-python/{__version__} python/{platform.python_version()}"
    if name == "snapshot_keys":
        assert "Authorization" not in request.headers
    else:
        assert request.headers["Authorization"] == f"Bearer {KEY}"
    if body is None:
        assert request.content == b""
        assert "Content-Type" not in request.headers
    else:
        assert json.loads(request.content) == body
        assert request.headers["Content-Type"] == "application/json"
    if method == "GET":
        assert "Idempotency-Key" not in request.headers
    else:
        assert request.headers["Idempotency-Key"]
    assert "Entitler-As-Of" not in request.headers
    assert "Entitler-Visitor" not in request.headers


def test_checks_are_typed_by_feature(transport: Transport) -> None:
    server = make(transport)
    transport.answers = [
        ok(answers.check(feature="ai_credits", type="metered", value=100, used=40, held=10)),
        ok(answers.check()),
    ]
    metered = server.customer("u").check(CREDITS)
    assert isinstance(metered, MeteredCheck)
    assert (metered.used, metered.held, metered.remaining) == (40, 10, 50)
    assert isinstance(server.customer("u").check(Feature.boolean("export_pdf")), BooleanCheck)


def test_replayed_is_read_from_the_exact_header(transport: Transport) -> None:
    server = make(transport)
    for header, expected in (("true", True), ("True", False), ("false", False), ("1", False)):
        transport.answers = [ok(answers.usage_result(), **{"Idempotent-Replayed": header})]
        result = server.customer("u").record_usage(CREDITS, 1, idempotency_key="k")
        assert result.replayed is expected
    transport.answers = [ok(answers.subscribe_step(), **{"Idempotent-Replayed": "true"})]
    step = server.customer("u").subscribe("pro")
    assert step.change is not None
    assert step.change.replayed is True
    transport.answers = [ok(answers.customer_track(), **{"Idempotent-Replayed": "true"})]
    assert server.customer("u").set_track("Beta").replayed is True
    assert server.customer("u").register().replayed is False


def test_as_of_is_sent_only_on_server_reads_given_it(transport: Transport) -> None:
    server = make(transport)
    customer = server.customer("u")
    instant = datetime(2026, 8, 1, 12, tzinfo=UTC)
    customer.check("sso", as_of=instant)
    customer.entitlements(as_of=instant)
    customer.plans(as_of=instant)
    customer.usage(as_of=instant)
    customer.details(as_of=instant)
    customer.is_entitled("sso", default=False, as_of=instant)
    for request in transport.requests:
        assert request.headers["Entitler-As-Of"] == "2026-08-01T12:00:00.000Z"
    transport.requests.clear()
    customer.record_usage(CREDITS, 1, idempotency_key="k")
    customer.pricing()
    assert all("Entitler-As-Of" not in r.headers for r in transport.requests)


@pytest.mark.parametrize(
    "value",
    [datetime(2026, 8, 1, 12), "2026-08-01T12:00:00Z", 1_782_898_200],  # noqa: DTZ001 - naive on purpose
)
def test_an_invalid_as_of_is_refused_before_any_request(value: Any, transport: Transport) -> None:
    customer = make(transport).customer("u")
    with pytest.raises((TypeError, ValueError), match="Pass as_of as a valid date."):
        customer.check("sso", as_of=value)
    assert transport.requests == []


def test_visitors_on_the_server_go_only_on_calls_given_one(transport: Transport) -> None:
    server = make(transport)
    visitor = server.new_visitor_id()
    assert len(visitor) == 32
    server.pricing(visitor=visitor)
    server.customer("u").register(visitor=visitor)
    server.customer("u").pricing(visitor=visitor)
    server.customer("u").check("sso")
    assert [r.headers.get("Entitler-Visitor") for r in transport.requests] == [visitor, visitor, visitor, None]
    with pytest.raises(ValueError, match="Pass visitor as an id"):
        server.pricing(visitor=visitor + "\n")


def test_in_app_client_sends_its_visitor_and_a_call_can_replace_it(transport: Transport) -> None:
    wired(transport)
    client = EntitlerClient(key="ent_pk_test_x", http_client=transport.client())
    assert len(client.visitor) == 32
    other = "v" * 20
    client.pricing()
    client.pricing(visitor=other, revalidate=True)
    assert [r.headers["Entitler-Visitor"] for r in transport.requests] == [client.visitor, other]
    kept = EntitlerClient(key="ent_pk_test_x", visitor="k" * 16, http_client=transport.client())
    assert kept.visitor == "k" * 16


def test_snapshot_keys_send_no_credential_even_when_the_provider_fails(transport: Transport) -> None:
    wired(transport)

    def failing() -> str:
        raise OSError("offline")

    client = EntitlerClient(token=failing, http_client=transport.client())
    keys = client.snapshot_keys()
    assert keys.keys == ()
    request = transport.requests[0]
    for header in ("Authorization", "Entitler-Identity-Token", "Entitler-Visitor", "Entitler-As-Of"):
        assert header not in request.headers


def test_identity_clients_send_the_identity_token(transport: Transport) -> None:
    wired(transport)
    token = "a.eyJleHAiOjk5OTk5OTk5OTl9.c"
    client = EntitlerClient(key=" ent_pk_test_x ", identity_token=token, http_client=transport.client())
    client.register(idempotency_key="sign-in-1")
    request = transport.requests[0]
    assert request.method == "PUT"
    assert request.url.path == "/customers/me"
    assert request.headers["Authorization"] == "Bearer ent_pk_test_x"
    assert request.headers["Entitler-Identity-Token"] == token
    assert request.headers["Idempotency-Key"] == "sign-in-1"
    assert client.scopes().registration is None
    transport.answers = [ok(answers.credential_self(scopes=["usage:write", "made:up"], registration=True))]
    found = client.scopes()
    assert [s.value for s in found.scopes] == ["usage:write"]
    assert found.registration is True


def test_the_in_app_customer_learns_its_id_from_answers(transport: Transport) -> None:
    wired(transport)
    client = EntitlerClient(token="a.b.c", http_client=transport.client())
    assert client.me.id is None
    transport.answers = [ok(answers.check(customer="google:42"))]
    client.me.check("sso")
    assert client.me.id == "google:42"
    assert repr(client.me) == "Customer(id='google:42')"


def test_customers_list_pages_lazily_from_a_cursor(transport: Transport) -> None:
    server = make(transport)
    first = answers.customer_page(items=[answers.customer_summary(external_id="a")], next="c2", used=3, limit=10)
    second = answers.customer_page(items=[], next="c3", used=3, limit=10)
    third = answers.customer_page(items=[answers.customer_summary(external_id="b")], used=3, limit=10)
    pager = server.customers.list(cursor="c1")
    assert transport.requests == []
    transport.answers = [ok(first), ok(second), ok(third)]
    assert [c.external_id for c in pager] == ["a", "b"]
    assert [r.url.params.get("cursor") for r in transport.requests] == ["c1", "c2", "c3"]
    transport.answers = [ok(first)]
    page = next(iter(server.customers.list().pages()))
    assert (page.used, page.limit, page.next) == (3, 10, "c2")


def test_usage_logs_page_lazily(transport: Transport) -> None:
    server = make(transport)
    event = answers.usage_event
    transport.answers = [
        ok(answers.customer_usage(log=[event(id="1")], next="n2")),
        ok(answers.customer_usage(log=[event(id="2")])),
    ]
    usage = server.customer("u").usage()
    assert [e.id for e in usage.log.items] == ["1"]
    assert usage.log.next == "n2"
    assert len(transport.requests) == 1
    assert [e.id for e in usage.log] == ["1", "2"]
    assert transport.requests[1].url.params["cursor"] == "n2"
    transport.answers = [
        ok(answers.customer_detail(usage=[event(id="3")], next="d2")),
        ok(answers.customer_detail(usage=[event(id="4")])),
    ]
    details = server.customer("u").details()
    assert [e.id for e in details.usage] == ["3", "4"]
    assert transport.requests[-1].url.params["cursor"] == "d2"


def test_create_answers_details_with_a_usage_log(transport: Transport) -> None:
    server = make(transport)
    transport.answers = [ok(answers.customer_detail(next="p2"), **{"Idempotent-Replayed": "true"})]
    created = server.customers.create("u", name="Ada")
    assert created.replayed is True
    assert created.usage.next == "p2"
    with pytest.raises(ValueError, match="Provide the customer's name."):
        server.customers.create("u", name=" ")


def test_erase_accepts_an_empty_answer(transport: Transport) -> None:
    server = make(transport)
    transport.answers = [{"status": 204}, ok(answers.customer_summary())]
    assert server.customer("u").erase() is None
    assert server.customer("u").erase() is None


@pytest.mark.parametrize(
    ("call", "message"),
    [
        (lambda c: c.check(""), "Name the feature by its key."),
        (lambda c: c.subscribe(" "), "Name the plan by its id or its key."),
        (lambda c: c.set_plan(None, product=" "), "Name the product by its key."),
        (lambda c: c.cancel(product=""), "Name the product by its key."),
        (lambda c: c.set_add_on("x", quantity=1, replaces=""), "Name the plan by its id or its key."),
        (lambda c: c.set_track(""), "Name the track by its name."),
        (lambda c: c.set_plan(None), "Pass product: the product whose plan ends."),
        (lambda c: c.set_plan(None, product="app", period="m"), "Pass no period, until or version when ending a plan."),
        (lambda c: c.set_plan("pro", product="app"), "Pass product only when ending a plan."),
        (lambda c: c.adjust_meter(CREDITS, idempotency_key="k"), "Pass either by, a whole number other than 0, or to, a whole number of 0 or more."),
        (lambda c: c.adjust_meter(CREDITS, by=0, idempotency_key="k"), "Pass either by, a whole number other than 0, or to, a whole number of 0 or more."),
        (lambda c: c.adjust_meter(CREDITS, by=1, to=1, idempotency_key="k"), "Pass either by, a whole number other than 0, or to, a whole number of 0 or more."),
        (lambda c: c.adjust_meter(CREDITS, to=-1, idempotency_key="k"), "Pass either by, a whole number other than 0, or to, a whole number of 0 or more."),
        (lambda c: c.record_usage(CREDITS, 0, idempotency_key="k"), "Pass amount as a whole number from 1 to 9007199254740991."),
        (lambda c: c.record_usage(CREDITS, 1.5, idempotency_key="k"), "Pass amount as a whole number from 1 to 9007199254740991."),
        (lambda c: c.record_usage(CREDITS, True, idempotency_key="k"), "Pass amount as a whole number from 1 to 9007199254740991."),
        (lambda c: c.settle_usage("h", -1), "Pass amount as a whole number from 0 to the held amount."),
        (lambda c: c.billing_portal(return_url=" "), "Pass return_url as the page to send the customer back to."),
        (lambda c: c.record_usage(CREDITS, 1, idempotency_key="k", occurred_at=datetime(2026, 1, 1)), "Pass occurred_at as a valid date."),  # noqa: DTZ001
        (lambda c: c.set_plan("pro", until=datetime(2026, 1, 1)), "Pass until as a valid date."),  # noqa: DTZ001
        (lambda c: c.grant("sso", value=-1), 'Pass value as a whole number from 0 to 999999999, or "unlimited".'),
        (lambda c: c.grant("sso", days=4000), "Pass days as a whole number from 0 to 3650."),
        (lambda c: c.set_add_on("x", quantity=10_001), "Pass quantity as a whole number from 0 to 10000."),
        (lambda c: c.subscribe("x", quantity=0), "Pass quantity as a whole number from 1 to 10000."),
        (lambda c: c.check("sso", timeout=0), "Pass timeout as a positive number of seconds."),
        (lambda c: c.check("sso", timeout=float("inf")), "Pass timeout as a positive number of seconds."),
        (lambda c: c.check("sso", timeout=float("nan")), "Pass timeout as a positive number of seconds."),
        (lambda c: c.is_entitled("sso", default=False, timeout=-1), "Pass timeout as a positive number of seconds."),
        (lambda c: c.is_entitled("sso", default="no"), "Pass default as True or False."),
        (lambda c: c.set_plan(Sku(connector="apple", ids={}), period="monthly"), "Pass no period with a SKU, which names its own."),
        (lambda c: c.set_plan(None, product="app", billing=PlanBilling.KEEP), "Pass billing as PlanBilling.PROVIDER or PlanBilling.END when ending a plan."),
    ],
)
def test_arguments_are_refused_before_any_request(call: Callable[[Any], Any], message: str, transport: Transport) -> None:
    customer = make(transport).customer("u")
    with pytest.raises((TypeError, ValueError)) as raised:
        call(customer)
    assert str(raised.value) == message
    assert transport.requests == []


def test_api_messages_pass_through(transport: Transport) -> None:
    server = make(transport)
    transport.answers = [{"status": 403, "body": answers.error("scope_required", "The key lacks tracks:assign.")}]
    with pytest.raises(APIError) as raised:
        server.customer("u").set_track("Beta")
    assert raised.value.message == "The key lacks tracks:assign."
    assert raised.value.code == "scope_required"
    assert raised.value.idempotency_key == transport.requests[0].headers["Idempotency-Key"]
    assert "scope_required" in repr(raised.value)


def test_injected_clients_never_follow_redirects(transport: Transport) -> None:
    seen: list[str] = []

    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(307, headers={"Location": "https://elsewhere.example.com/"})

    client = httpx.Client(transport=httpx.MockTransport(handle), follow_redirects=True)
    server = EntitlerServer(KEY, http_client=client, max_retries=0)
    with pytest.raises(APIError) as raised:
        server.customer("u").record_usage(CREDITS, 1, idempotency_key="k")
    assert raised.value.status == 307
    assert raised.value.code == "http_error"
    assert seen == ["https://api.entitler.dev/customers/u/usage"]
