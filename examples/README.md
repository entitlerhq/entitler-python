# Examples

Small programs that use the SDK. Each runs against your Entitler environment with a secret key in
`ENTITLER_KEY`, creates its own customer and erases it at the end.

| Example | What it shows | Run |
| --- | --- | --- |
| [quickstart](quickstart/main.py) | A server registers a customer (`ENTITLER_CUSTOMER_ID`, or a new one it erases), checks a feature and records usage | `ENTITLER_KEY=… python examples/quickstart/main.py` |
| [pricing-page](pricing-page/main.py) | A signed-out pricing page, through `EntitlerClient(key=…)` and the server with a visitor id | `ENTITLER_KEY=… ENTITLER_PUBLISHABLE_KEY=… python examples/pricing-page/main.py` |
| [in-app](in-app/main.py) | A customer token minted for the signed-in customer, used by an in-app client closed at sign-out | `ENTITLER_KEY=… python examples/in-app/main.py` |
| [metered-work](metered-work/main.py) | `start_hold` around streamed work, `with_hold` around a job, and an observe-mode report | `ENTITLER_KEY=… python examples/metered-work/main.py` |
| [offline](offline/main.py) | A snapshot verified offline, and the fall-back when Entitler is unreachable | `ENTITLER_KEY=… python examples/offline/main.py` |
| [billing](billing/main.py) | A billing page from `plans()`, every next step, the return page, the portal, `set_plan` and a grant | `ENTITLER_KEY=… python examples/billing/main.py` |
| [generated-features](generated-features/main.py) | Constants written by `python -m entitler generate` | `ENTITLER_KEY=… python examples/generated-features/main.py` |
| [fastapi](fastapi/app.py) | A FastAPI app: a lifespan client, a customer dependency, gating and a token route | `ENTITLER_KEY=… uvicorn app:app` from `examples/fastapi` |
| [testing](testing/test_gating.py) | Unit tests of an app's gating and billing code with `fake_customer` | `pytest examples/testing` |

Install the SDK first (`pip install "entitler[snapshots]"`, or `uv sync` in this repository).
