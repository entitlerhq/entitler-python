"""Tests of the app's gating and billing code, with no network: run `pytest examples/testing`."""

import pytest
from gating import UpgradeRequiredError, checkout, export_pdf, summarise, upgrade_buttons

from entitler.testing import answers, fake_customer, metered


def test_export_needs_the_feature() -> None:
    with pytest.raises(UpgradeRequiredError):
        export_pdf(fake_customer({"export_pdf": False}), "report")
    assert export_pdf(fake_customer({"export_pdf": True}), "report") == b"report"


def test_a_summary_charges_what_it_used() -> None:
    customer = fake_customer({"ai_credits": metered(value=100, used=10)})
    summary = summarise(customer, "job-1", "one two three four five six seven")
    assert summary == "one two three four five"
    assert customer.check("ai_credits").used == 15
    assert [write.method for write in customer.writes] == ["hold_usage", "settle_usage"]


def test_an_unknown_feature_fails_closed() -> None:
    with pytest.raises(UpgradeRequiredError):
        export_pdf(fake_customer({}), "report")


def test_the_paywall_offers_the_moves_plans_lists() -> None:
    plans = answers.customer_plans(
        options=[
            answers.move_option(),
            answers.move_option(
                plan={
                    "id": "pln_ent",
                    "key": "enterprise",
                    "name": "Enterprise",
                    "kind": "plan",
                    "description": "",
                    "default": False,
                },
                action="contact",
            ),
        ]
    )
    customer = fake_customer({}, plans=plans)
    assert upgrade_buttons(customer) == ["Upgrade to Pro", "Talk to sales"]


def test_checkout_sends_the_customer_to_pay() -> None:
    customer = fake_customer({})
    assert checkout(customer, "pro", "https://app.example.com/billing/return") is None
    customer.answer("subscribe", lambda call: answers.subscribe_step(next="pay", url="https://pay"))
    assert checkout(customer, "pro", "https://app.example.com/billing/return") == "https://pay"
    assert customer.writes[-1].arguments["plan"] == "pro"
