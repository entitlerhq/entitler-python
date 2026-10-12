"""Snapshot verification with keys made here (section 9) and the testing entry point (5.6)."""

from __future__ import annotations

import base64
import builtins
import json
from datetime import UTC, datetime
from typing import Any

import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature

from entitler import (
    APIError,
    Feature,
    NextStep,
    SnapshotError,
    SnapshotKey,
    SnapshotKeys,
    UsageRefusedError,
    UsageReplayedError,
    verify_snapshot,
)
from entitler.testing import FakeCall, answers, fake_async_customer, fake_customer, metered

NOW = datetime(2026, 7, 2, 12, tzinfo=UTC)
IAT = int(datetime(2026, 7, 1, 9, 30, tzinfo=UTC).timestamp())
CREDITS = Feature.metered("ai_credits")


def b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


class Signer:
    def __init__(self, kid: str = "test-key") -> None:
        self.kid = kid
        self.private = ec.generate_private_key(ec.SECP256R1())
        numbers = self.private.public_key().public_numbers()
        self.jwk = {
            "kty": "EC",
            "crv": "P-256",
            "x": b64(numbers.x.to_bytes(32, "big")),
            "y": b64(numbers.y.to_bytes(32, "big")),
            "kid": kid,
            "alg": "ES256",
            "use": "sig",
        }

    def sign(self, claims: dict[str, Any], header: dict[str, Any] | None = None) -> str:
        head = b64(
            json.dumps(
                header or {"typ": "entitlements+jwt", "alg": "ES256", "kid": self.kid}
            ).encode()
        )
        body = b64(json.dumps(claims).encode())
        der = self.private.sign(f"{head}.{body}".encode(), ec.ECDSA(hashes.SHA256()))
        r, s = decode_dss_signature(der)
        return f"{head}.{body}.{b64(r.to_bytes(32, 'big') + s.to_bytes(32, 'big'))}"


def claims(**changes: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "iss": "https://api.entitler.dev/customers",
        "sub": "user_1",
        "environment": {"id": "env_1"},
        "track": {"id": "trk", "name": "All customers"},
        "release": 2,
        "change": None,
        "testers": True,
        "entitlements": [
            {
                "key": "export_pdf",
                "type": "boolean",
                "value": True,
                "entitled": True,
                "sources": [],
            },
            {
                "key": "ai_credits",
                "type": "metered",
                "value": 100,
                "entitled": True,
                "sources": [],
                "used": 10,
                "held": 0,
                "remaining": 90,
                "resetsAt": None,
            },
        ],
        "iat": IAT,
        "exp": IAT + 7 * 86400,
    }
    base.update(changes)
    return base


def verify(token: str, signer: Signer, **changes: Any) -> Any:
    options: dict[str, Any] = {
        "keys": [signer.jwk],
        "customer": "user_1",
        "environment": "env_1",
        "now": NOW,
    }
    options.update(changes)
    return verify_snapshot(token, **options)


def test_a_valid_snapshot_verifies_with_a_key_set_or_list() -> None:
    signer = Signer()
    token = signer.sign(claims())
    found = verify(token, signer)
    assert found.customer == "user_1"
    assert found.entitlements.has("export_pdf")
    assert found.entitlements.get("ai_credits") is not None
    assert found.entitlements.as_of == datetime.fromtimestamp(IAT, UTC)
    assert found.entitlements.experiment is None
    assert found.environment.id == "env_1"
    key_set = SnapshotKeys(keys=(SnapshotKey(**signer.jwk),))
    assert verify(token, signer, keys=key_set).release == 2


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (
            {"iss": "https://evil.example.com"},
            "This snapshot was not issued by https://api.entitler.dev/customers.",
        ),
        (
            {"sub": "user_2"},
            "This snapshot is for another customer, not user_1. "
            "Fetch one for the signed-in customer while online.",
        ),
        (
            {"environment": {"id": "env_2"}},
            "This snapshot is from another environment, not env_1. "
            "Fetch one from your app's environment while online.",
        ),
        (
            {"iat": int(NOW.timestamp()) + 61},
            "This snapshot was signed for 2026-07-02T12:01:01.000Z, which is still to come. "
            "Fetch a new one while online.",
        ),
        (
            {"testers": None},
            "That is not an entitlements snapshot. Pass the token snapshot() returned.",
        ),
    ],
)
def test_each_check_fails_with_its_message(change: dict[str, Any], message: str) -> None:
    signer = Signer()
    with pytest.raises(SnapshotError) as raised:
        verify(signer.sign(claims(**change)), signer)
    assert raised.value.message == message
    assert raised.value.code == "snapshot_invalid"


def test_payments_without_testers_is_refused() -> None:
    signer = Signer()
    old = claims(payments=True)
    del old["testers"]
    with pytest.raises(SnapshotError):
        verify(signer.sign(old), signer)


def test_an_expired_snapshot_says_when() -> None:
    signer = Signer()
    with pytest.raises(SnapshotError) as raised:
        verify(signer.sign(claims(exp=IAT + 60)), signer)
    assert raised.value.code == "snapshot_expired"
    assert (
        raised.value.message
        == "This snapshot expired at 2026-07-01T09:31:00.000Z. Fetch a new one while online."
    )


def test_skew_bounds() -> None:
    signer = Signer()
    ahead = signer.sign(claims(iat=int(NOW.timestamp()) + 300))
    assert verify(ahead, signer, clock_skew_seconds=300)
    with pytest.raises(SnapshotError):
        verify(ahead, signer, clock_skew_seconds=299)
    for bad in (-1, 301, 1.5, True):
        with pytest.raises(
            (TypeError, ValueError),
            match=r"Pass clock_skew_seconds as a whole number from 0 to 300\.",
        ):
            verify(ahead, signer, clock_skew_seconds=bad)


def test_now_is_read_at_each_verification_and_must_be_an_instant(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from entitler import _clock

    signer = Signer()
    token = signer.sign(claims())
    monkeypatch.setattr(_clock, "now", lambda: NOW.timestamp())
    assert verify(token, signer, now=None)
    monkeypatch.setattr(_clock, "now", lambda: float(IAT + 8 * 86400))
    with pytest.raises(SnapshotError):
        verify(token, signer, now=None)
    with pytest.raises(ValueError, match=r"Pass now as a valid instant\."):
        verify(token, signer, now=datetime(2026, 7, 2))
    with pytest.raises((TypeError, ValueError)):
        verify(token, signer, customer=" ")


def test_a_wrong_key_and_a_tampered_token_fail() -> None:
    signer = Signer()
    other = Signer("other")
    token = signer.sign(claims())
    with pytest.raises(SnapshotError, match="None of the keys passed signed this snapshot"):
        verify(token, signer, keys=[other.jwk])
    impostor = Signer("test-key")
    with pytest.raises(SnapshotError, match="changed after Entitler signed it"):
        verify(token, signer, keys=[impostor.jwk])
    head, _body, sig = token.split(".")
    tampered = f"{head}.{b64(json.dumps(claims(sub='user_9')).encode())}.{sig}"
    with pytest.raises(SnapshotError, match="changed after Entitler signed it"):
        verify(tampered, signer)
    with pytest.raises(SnapshotError, match="Pass the token snapshot"):
        verify(None, signer)  # type: ignore[arg-type]  # a value the call must refuse or accept as a string


def test_a_missing_cryptography_package_says_how_to_install_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real = builtins.__import__

    def refuse(name: str, *args: Any, **kwargs: Any) -> Any:
        if name.startswith("cryptography"):
            raise ImportError(name)
        return real(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", refuse)
    signer = Signer.__new__(Signer)
    with pytest.raises(ImportError, match=r"install entitler\[snapshots\]"):
        verify_snapshot("a.b.c", keys=[], customer="u", environment="e", now=NOW)
    del signer


def test_the_fake_customer_reads_and_meters() -> None:
    customer = fake_customer(
        {
            "export_pdf": True,
            "sso": False,
            "team_seats": 5,
            "ai_credits": metered(value=100, used=97),
        },
        id="user_7",
    )
    assert customer.id == "user_7"
    assert customer.check("export_pdf").entitled
    assert not customer.is_entitled("sso", default=True)
    assert customer.check("team_seats").value == 5
    assert not customer.is_entitled("unknown", default=False)
    with pytest.raises(APIError) as raised:
        customer.check("unknown")
    assert raised.value.code == "feature_not_found"
    assert len(customer.entitlements()) == 4
    refused = customer.record_usage(CREDITS, 5, idempotency_key="a")
    assert (refused.outcome, refused.refusal) == ("refused", "over_allowance")
    recorded = customer.record_usage(CREDITS, 3, idempotency_key="a")
    assert (recorded.outcome, recorded.meter_change, recorded.remaining) == ("recorded", 3, 0)
    replay = customer.record_usage(CREDITS, 3, idempotency_key="a")
    assert (replay.outcome, replay.meter_change, replay.replayed) == ("duplicate", 0, True)
    with pytest.raises(APIError) as mismatch:
        customer.record_usage(CREDITS, 2, idempotency_key="a")
    assert mismatch.value.code == "idempotency_mismatch"
    observed = customer.record_usage(CREDITS, 10, idempotency_key="b", mode="observe")  # type: ignore[arg-type]  # a value the call must refuse or accept as a string
    assert observed.over_by == 10
    cancelled = customer.cancel_usage(recorded.id or "")
    assert (cancelled.outcome, cancelled.meter_change) == ("cancelled", -3)
    assert customer.record_usage(CREDITS, 3, idempotency_key="a").outcome == "cancelled"
    adjusted = customer.adjust_meter(CREDITS, to=0, idempotency_key="c")
    assert adjusted.outcome == "adjusted"
    assert customer.check(CREDITS).used == 0
    log = customer.usage().log.items
    assert [entry.kind for entry in log] == ["use", "use", "adjust"]
    assert log[0].cancelled_at is not None
    assert len(customer.details().usage.items) == 3
    with pytest.raises(APIError) as not_metered:
        customer.record_usage(Feature.metered("sso"), 1, idempotency_key="d")
    assert not_metered.value.code == "not_metered"


def test_the_fake_settles_and_releases_holds_as_the_api_does() -> None:
    customer = fake_customer({"ai_credits": metered(value=100)})
    hold = customer.start_hold(CREDITS, 10, idempotency_key="h1")
    assert customer.check(CREDITS).held == 10
    again = customer.start_hold(CREDITS, 10, idempotency_key="h1")
    assert again.duplicate
    with pytest.raises(APIError) as too_much:
        customer.settle_usage(hold.id, 11)
    assert too_much.value.code == "invalid_body"
    assert customer.settle_usage(hold.id, 4).outcome == "settled"
    assert customer.settle_usage(hold.id, 4).outcome == "duplicate"
    with pytest.raises(APIError) as other:
        customer.settle_usage(hold.id, 5)
    assert other.value.code == "hold_settled"
    with pytest.raises(APIError) as settled:
        customer.release_usage(hold.id)
    assert settled.value.code == "hold_settled"
    with pytest.raises(UsageReplayedError):
        customer.start_hold(CREDITS, 10, idempotency_key="h1")
    second = customer.start_hold(CREDITS, 5, idempotency_key="h2")
    assert customer.release_usage(second.id).outcome == "released"
    assert customer.release_usage(second.id).outcome == "released"
    with pytest.raises(APIError) as released:
        customer.settle_usage(second.id, 1)
    assert released.value.code == "hold_released"
    assert customer.check(CREDITS).used == 4
    assert [w.method for w in customer.writes][:2] == ["hold_usage", "hold_usage"]


def test_the_fake_checks_a_holds_state_before_its_amount() -> None:
    customer = fake_customer({"ai_credits": metered(value=100)})
    hold = customer.start_hold(CREDITS, 10, idempotency_key="h1")
    customer.settle_usage(hold.id, 4)
    with pytest.raises(APIError) as settled:
        customer.settle_usage(hold.id, 50)
    assert settled.value.code == "hold_settled"
    released = customer.start_hold(CREDITS, 10, idempotency_key="h2")
    customer.release_usage(released.id)
    with pytest.raises(APIError) as gone:
        customer.settle_usage(released.id, 50)
    assert gone.value.code == "hold_released"


def test_the_fake_keeps_the_usage_log_as_the_api_does() -> None:
    customer = fake_customer({"ai_credits": metered(value=100)})
    report = customer.record_usage(CREDITS, 3, idempotency_key="r1")
    nothing = customer.start_hold(CREDITS, 10, idempotency_key="h1")
    customer.settle_usage(nothing.id, 0)
    freed = customer.start_hold(CREDITS, 10, idempotency_key="h2")
    customer.release_usage(freed.id)
    used = customer.start_hold(CREDITS, 10, idempotency_key="h3")
    customer.settle_usage(used.id, 6)
    customer.adjust_meter(CREDITS, by=-2, idempotency_key="a1", actor="agent_7", reason="Refund")
    customer.cancel_usage(report.id or "", actor="agent_7", reason="Duplicate charge")
    log = customer.usage().log.items
    assert [(entry.kind, entry.amount) for entry in log] == [("use", 3), ("use", 6), ("adjust", -2)]
    assert (log[0].actor, log[0].reason) == ("agent_7", "Duplicate charge")
    assert log[0].cancelled_at is not None
    assert (log[2].actor, log[2].reason) == ("agent_7", "Refund")


def test_the_fake_expires_holds(monkeypatch: pytest.MonkeyPatch) -> None:
    from entitler import _clock

    now = [1_000_000.0]
    monkeypatch.setattr(_clock, "now", lambda: now[0])
    customer = fake_customer({"ai_credits": metered(value=100)})
    hold = customer.start_hold(CREDITS, 10, idempotency_key="h", ttl_seconds=60)
    hold.use(12)
    now[0] += 61
    assert hold.finish().outcome == "recorded"
    assert customer.check(CREDITS).used == 12


def test_the_fake_answers_every_method_and_records_writes() -> None:
    plans = answers.customer_plans(customer="user_1")
    customer = fake_customer({"sso": True}, plans=plans, pricing=answers.pricing(customer="user_1"))
    assert customer.plans().customer == "user_1"
    assert customer.pricing().customer == "user_1"
    assert customer.subscribe("pro").next == NextStep.DONE
    assert customer.cancel().changed
    assert customer.undo_pending_change(add_on="x").changed
    assert customer.set_plan("pro").plan is not None
    assert customer.set_plan(None, product="app").changed
    assert customer.set_add_on("seats", quantity=3).quantity == 3
    grant = customer.grant("sso", days=3).grant
    assert customer.revoke_grant(grant.id).grant.revoked_at is not None
    assert customer.register().created
    assert not customer.register().created
    assert customer.update(name="Ada").name == "Ada"
    assert customer.token().customer == "user_1"
    assert customer.snapshot().token
    assert customer.set_track("Beta").track.name == "Beta"
    assert customer.set_track(None).track.name == "All customers"
    assert customer.billing().items == 0
    assert customer.billing_portal(return_url="https://app.example.com").url
    assert not customer.sync_billing().changed
    customer.erase()
    methods = [w.method for w in customer.writes]
    assert methods[:3] == ["subscribe", "cancel", "undo_pending_change"]
    assert methods[-1] == "erase"
    assert customer.writes[0].arguments == {"plan": "pro"}


def test_answers_can_be_replaced_per_method() -> None:
    customer = fake_customer({})
    customer.answer("subscribe", lambda _call: answers.subscribe_step(next="pay"))
    step = customer.subscribe("pro", return_url="https://app.example.com")
    assert (step.next, step.url) == (NextStep.PAY, "https://checkout.example.com/pay")

    def declined(call: FakeCall) -> dict[str, Any]:
        raise APIError(status=402, code="payment_required", message="Declined.")

    customer.answer("subscribe", declined)
    with pytest.raises(APIError) as raised:
        customer.subscribe("pro")
    assert raised.value.status == 402


@pytest.mark.anyio
async def test_the_async_fake_answers_too() -> None:
    customer = fake_async_customer({"ai_credits": metered(value=10)})
    async with customer.with_hold(CREDITS, 5, idempotency_key="j") as hold:
        hold.use(2)
    assert (await customer.check(CREDITS)).used == 2
    assert [w.method for w in customer.writes] == ["hold_usage", "settle_usage"]


def test_a_settlement_in_the_fake_is_a_report_that_can_be_cancelled() -> None:
    customer = fake_customer({"ai_credits": metered(value=100)})
    hold = customer.start_hold(CREDITS, 10, idempotency_key="h1")
    settled = customer.settle_usage(hold.id, 6)
    assert settled.id is not None
    assert customer.usage().log.items[0].id == settled.id
    cancelled = customer.cancel_usage(settled.id)
    assert (cancelled.outcome, cancelled.meter_change) == ("cancelled", -6)
    assert customer.check(CREDITS).used == 0


@pytest.mark.parametrize("hold", [False, True], ids=["report", "hold"])
def test_the_fake_names_the_refusal_as_the_api_does(hold: bool) -> None:
    def refusal(values: dict[str, Any]) -> object:
        customer = fake_customer(values)
        if hold:
            with pytest.raises(UsageRefusedError) as raised:
                customer.start_hold(CREDITS, 5, idempotency_key="k")
            return raised.value.result.refusal
        return customer.record_usage(CREDITS, 5, idempotency_key="k").refusal

    assert refusal({"ai_credits": metered(value=0)}) == "not_entitled"
    assert refusal({"ai_credits": metered(value=10, used=10)}) == "over_allowance"
    assert refusal({"ai_credits": metered(value=10, used=8)}) == "over_allowance"
