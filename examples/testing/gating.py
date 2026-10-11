"""App code under test: gating an export, and charging for a summary."""

from entitler import Customer, Feature

EXPORT_PDF = Feature.boolean("export_pdf")
AI_CREDITS = Feature.metered("ai_credits")


class UpgradeRequiredError(Exception):
    """The customer's plan does not include the feature."""


def export_pdf(customer: Customer, document: str) -> bytes:
    """Renders a document as PDF for customers entitled to it."""
    if not customer.is_entitled(EXPORT_PDF, default=False):
        raise UpgradeRequiredError
    return document.encode()


def summarise(customer: Customer, job_id: str, text: str) -> str:
    """Summarises text, charging one credit per word of the summary."""
    with customer.with_hold(AI_CREDITS, 50, idempotency_key=job_id) as hold:
        summary = " ".join(text.split()[:5])
        hold.use(len(summary.split()))
    return summary
