"""App code under test: gating an export, and charging for a summary."""

from entitler import Customer, Feature, NextStep, UsageRefusedError, UsageReplayedError

EXPORT_PDF = Feature.boolean("export_pdf")
AI_CREDITS = Feature.metered("ai_credits")


class UpgradeRequiredError(Exception):
    """The customer's plan does not include the feature."""


def export_pdf(customer: Customer, document: str) -> bytes:
    """Renders a document as PDF for customers entitled to it."""
    if not customer.is_entitled(EXPORT_PDF, default=False):
        raise UpgradeRequiredError
    return document.encode()


class AlreadyDoneError(Exception):
    """The job already ran: show its result rather than run it again."""


def summarise(customer: Customer, job_id: str, text: str) -> str:
    """Summarises text, charging one credit per word of the summary."""
    try:
        with customer.with_hold(AI_CREDITS, 50, idempotency_key=job_id) as hold:
            summary = " ".join(text.split()[:5])
            hold.use(len(summary.split()))
    except UsageRefusedError as refused:
        raise UpgradeRequiredError from refused
    except UsageReplayedError as replayed:
        raise AlreadyDoneError from replayed
    return summary


def upgrade_buttons(customer: Customer) -> list[str]:
    """The paywall's buttons, from the moves `plans()` offers."""
    return [
        f"Upgrade to {option.plan.name}" if option.action == "buy" else "Talk to sales"
        for option in customer.plans().options
        if option.action in ("buy", "contact")
    ]


def checkout(customer: Customer, plan: str, return_url: str) -> str | None:
    """Starts a plan change: the page to send the customer to, or `None` when it is done."""
    step = customer.subscribe(plan, period="monthly", return_url=return_url)
    match step.next:
        case NextStep.PAY:
            return step.url
        case NextStep.DONE:
            return None
        case _:
            raise UpgradeRequiredError
