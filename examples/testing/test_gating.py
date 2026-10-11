"""Tests of the app's gating and billing code, with no network: run `pytest examples/testing`."""

import pytest
from gating import UpgradeRequiredError, export_pdf, summarise

from entitler.testing import fake_customer, metered


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
