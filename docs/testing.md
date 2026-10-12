# Testing your app

Test your own gating and billing code without the network, in one of two ways.

## `fake_customer`

`entitler.testing.fake_customer` answers a real `ServerCustomer` whose requests go to an
in-process fake of the API. Write your app's functions to take a `Customer`, and pass the fake:

```python
from entitler import Customer
from entitler.testing import answers, fake_customer, metered


def can_export(customer: Customer) -> bool:
    return customer.is_entitled(features.EXPORT_PDF, default=False)


def test_export_needs_the_feature() -> None:
    assert not can_export(fake_customer({"export_pdf": False}))
    assert can_export(fake_customer({"export_pdf": True}))


def test_usage_moves_the_meter() -> None:
    fake = fake_customer({"ai_credits": metered(value=100, used=97)})
    fake.record_usage(features.AI_CREDITS, 2, idempotency_key="job-1")
    refused = fake.record_usage(features.AI_CREDITS, 2, idempotency_key="job-2")
    assert refused.outcome == "refused"
    assert [write.method for write in fake.writes] == ["record_usage", "record_usage"]
```

- `values` maps feature keys to `True` or `False`, an amount or `"unlimited"`, or
  `metered(value=..., used=...)`. A key it does not hold answers `404 feature_not_found`, so
  `is_entitled` answers its default.
- Usage writes move the meters as Entitler does: a gated report past the allowance is refused,
  holds settle and release, a reused key replays, and `cancel_usage` moves the meter back. Every
  write that moves a meter adds to the usage log `usage()` and `details()` answer.
- `plans=` and `pricing=` set the `plans()` and `pricing()` answers, built with
  `entitler.testing.answers`.
- `writes` lists every write in order, with its `method`, `arguments` (as the request carried
  them) and `idempotency_key`.
- `answer(method, fn)` replaces one method's answer: `fn` receives the call and answers a body
  from the builders, or raises an `APIError`.

```python
from entitler import NextStep


def test_checkout_sends_the_customer_to_pay() -> None:
    fake = fake_customer({})
    fake.answer("subscribe", lambda call: answers.subscribe_step(next="pay", url="https://pay"))
    step = fake.subscribe("pro", period="monthly", return_url="https://app.example.com/back")
    assert step.next == NextStep.PAY
    assert fake.writes[-1].arguments == {
        "plan": "pro",
        "period": "monthly",
        "returnUrl": "https://app.example.com/back",
    }
```

`fake_async_customer` is the `AsyncServerCustomer` twin. The package never imports
`entitler.testing` itself, so it costs nothing in production.

## Answer builders on a fake transport

To test code that builds its own client, fake the HTTP transport (with `respx` or
`httpx.MockTransport`) and answer with the builders in `entitler.testing.answers`. Each writes a
complete body, so a fixture missing one field never fails to decode and passes a test for the wrong
reason.

```python
import httpx


def test_a_check_through_a_fake_transport() -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=answers.check(feature="sso", entitled=False))

    test_server = EntitlerServer(
        "ent_test_fake",
        max_retries=0,
        cache=None,
        http_client=httpx.Client(transport=httpx.MockTransport(handle)),
    )
    assert not test_server.customer("user_1").is_entitled("sso", default=True)
```

Give test clients `max_retries=0` and `cache=None`, so a failure answers at once and every call
reaches the fake. The SDK reads the system clock for token expiry and the cache, so a test that
needs time to pass sets the times in its fixtures (`answers.instant(...)`, a token's `exp`) rather
than freezing the clock.
