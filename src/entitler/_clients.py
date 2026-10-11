"""The blocking clients: `EntitlerServer` and `EntitlerClient`."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
from types import TracebackType
from typing import Literal, overload

import httpx

from ._cache import CacheStore, MemoryCache
from ._customer import Customer, ServerCustomer
from ._io import SyncCredentials, SyncDriver, SyncToken, TokenProvider
from ._models import (
    CustomerDetail,
    FeatureList,
    Page,
    Pricing,
    RegisteredCustomer,
    Scopes,
    SnapshotKeys,
    UsageEvent,
    UsageEventInput,
    UsageEventsResult,
)
from ._ops import (
    BatchPlan,
    CustomerOps,
    ServerOps,
    call_timeout,
    create_customer,
    customer_page,
    features,
    pricing,
    record_batch,
    scopes,
    snapshot_keys,
)
from ._pages import CustomerPager, UsageLog
from ._settings import (
    SIGN_IN,
    Settings,
    in_app_credentials,
    server_key,
)
from ._snapshot import VerifiedSnapshot, verify_snapshot
from ._values import CLIENT_TIMEOUT, CUSTOMER_ID, require_id
from ._visitor import new_visitor_id


class Customers:
    """The customer list, and creating customers."""

    def __init__(self, driver: SyncDriver) -> None:
        self._driver = driver

    def list(
        self,
        *,
        q: str | None = None,
        cohort: str | None = None,
        track: str | None = None,
        include_test: bool = False,
        cursor: str | None = None,
        timeout: float = CLIENT_TIMEOUT,
    ) -> CustomerPager:
        """Every customer matching the options, a page at a time, each fetched when reached.

        Args:
            q: Searches names, emails and external ids.
            cohort: Only customers holding this cohort, named `<plan key or id>:<n>`.
            track: Only customers on the track with this id.
            include_test: Include test customers.
            cursor: Start at the page a previous page's `next` names, so a stateless admin page
                can link to page 3.
            timeout: Each request's per-attempt timeout in seconds.
        """
        seconds = call_timeout(timeout)
        engine = self._driver.engine
        return CustomerPager(
            lambda next_page: self._driver.run(
                customer_page(engine, next_page, q, cohort, track, include_test, seconds)
            ),
            cursor,
        )

    def create(
        self,
        external_id: str,
        *,
        name: str,
        email: str | None = None,
        plan: str | None = None,
        period: str | None = None,
        metadata: Mapping[str, str] | None = None,
        idempotency_key: str | None = None,
        timeout: float = CLIENT_TIMEOUT,
    ) -> CustomerDetail[UsageLog]:
        """Creates a customer, optionally on a plan, and answers their details."""
        engine = self._driver.engine
        ops = ServerOps(engine, require_id(external_id, CUSTOMER_ID))

        def log(first: Page[UsageEvent]) -> UsageLog:
            return UsageLog(
                first, lambda cursor: self._driver.run(ops.details_page(cursor, timeout, None))
            )

        return self._driver.run(
            create_customer(
                engine,
                external_id,
                name,
                email,
                plan,
                period,
                metadata,
                idempotency_key,
                timeout,
                log,
            )
        )


class EntitlerServer:
    """The server client, built from a secret project key. It runs on your servers.

    It acts on any customer. Keep one per process and share it between threads; close it, or use
    it as a context manager, when the process stops.

    Example:
        ```python
        server = EntitlerServer(os.environ["ENTITLER_KEY"])
        customer = server.customer(user.id)
        customer.register(name=user.name, email=user.email)
        if customer.is_entitled(features.EXPORT_PDF, default=False):
            export_pdf()
        customer.record_usage(features.AI_CREDITS, 3, idempotency_key=job.id)
        ```
    """

    def __init__(
        self,
        key: str,
        *,
        base_url: str = "https://api.entitler.dev",
        timeout: float = 10.0,
        max_retries: int = 2,
        max_retry_delay: float = 10.0,
        cache: CacheStore | int | None = 1000,
        stale_for: float = 86400.0,
        on_error: OnError | None = None,
        http_client: httpx.Client | None = None,
    ) -> None:
        """Creates a server client.

        Args:
            key: A secret key from the dashboard. A publishable key (`ent_pk_…`) belongs in
                `EntitlerClient` and is refused.
            base_url: The API's base URL.
            timeout: Each attempt's deadline in seconds, per phase as httpx measures it.
            max_retries: How many times to retry after the first attempt.
            max_retry_delay: The longest `Retry-After`, in seconds, the client waits for.
            cache: The answer cache: the in-memory store's size, your own `CacheStore` (Redis,
                for one) to share answers between processes, or `None` for none.
            stale_for: How long, in seconds, a kept answer may stand in while Entitler is
                unreachable.
            on_error: Called with each error a fallback absorbed: a stale answer, an `is_entitled`
                default, a hold's failed release or disposal, and a custom store's own failures.
            http_client: An `httpx.Client` to send requests with, for proxies and
                instrumentation. The client never closes it. It must not use a caching transport.

        Raises:
            ValueError: the key is blank or publishable, or an option is out of range.
        """
        secret = server_key(key)
        settings = Settings(base_url, timeout, max_retries, max_retry_delay, stale_for, on_error)
        store = settings.store(cache, custom=True)
        self._owns_store = isinstance(cache, int) and not isinstance(cache, bool)
        engine = settings.engine(cached=store is not None)
        self._driver = SyncDriver(engine, SyncCredentials("server", secret), store, http_client)
        self.customers = Customers(self._driver)
        """The customer list, and creating customers."""

    def customer(self, customer_id: str) -> ServerCustomer:
        """The customer with the id your app uses for them. Makes no request.

        Raises:
            ValueError: the id is blank or made only of dots, or is not valid Unicode.
        """
        ops = ServerOps(self._driver.engine, require_id(customer_id, CUSTOMER_ID))
        return ServerCustomer(ops, self._driver)

    def record_usage_batch(
        self,
        events: Sequence[UsageEventInput],
        *,
        register: bool | None = None,
        timeout: float = CLIENT_TIMEOUT,
    ) -> UsageEventsResult:
        """Records many usage events in observe mode, each under its own idempotency key.

        Events go in requests of at most 500, in order. It answers one result per event, in input
        order, and never raises because of Entitler: an event the SDK refuses itself is answered
        `error` and not sent, and a request that fails after its retries answers its events
        `error` (`connection_failed` or `timed_out` when no answer arrived) while the next request
        still goes. Each request's idempotency key derives from its events, so resending the
        events answered `error` is always safe.
        """
        seconds = call_timeout(timeout)
        self._driver.engine.ensure_open()
        plan = BatchPlan(list(events), register)
        return self._driver.run(record_batch(self._driver.engine, plan, seconds))

    def pricing(
        self,
        *,
        visitor: str | None = None,
        revalidate: bool = False,
        timeout: float = CLIENT_TIMEOUT,
    ) -> Pricing:
        """The pricing on sale, signed out, through the answer cache.

        Pass the visitor's id from `new_visitor_id()` to keep their experiment arm.
        """
        return self._driver.run(pricing(self._driver.engine, visitor, revalidate, timeout))

    def features(self, *, revalidate: bool = False, timeout: float = CLIENT_TIMEOUT) -> FeatureList:
        """The catalogue's features, through the answer cache."""
        return self._driver.run(features(self._driver.engine, revalidate, timeout))

    def scopes(self, *, timeout: float = CLIENT_TIMEOUT) -> Scopes:
        """The scopes this client's key holds, asked afresh on each call."""
        return self._driver.run(scopes(self._driver.engine, timeout, identity=False))

    def snapshot_keys(self, *, timeout: float = CLIENT_TIMEOUT) -> SnapshotKeys:
        """The public keys that verify snapshots. Sends no credential."""
        return self._driver.run(snapshot_keys(self._driver.engine, timeout))[0]

    @staticmethod
    def verify_snapshot(
        token: str,
        *,
        keys: SnapshotKeys | Sequence[object],
        customer: str,
        environment: str,
        issuer: str = "https://api.entitler.dev/customers",
        now: datetime | None = None,
        clock_skew_seconds: int = 60,
    ) -> VerifiedSnapshot:
        """Verifies a snapshot offline, with no request. See `entitler.verify_snapshot`."""
        return verify_snapshot(
            token,
            keys=keys,
            customer=customer,
            environment=environment,
            issuer=issuer,
            now=now,
            clock_skew_seconds=clock_skew_seconds,
        )

    @staticmethod
    def new_visitor_id() -> str:
        """Mints a new visitor id. See `entitler.new_visitor_id`."""
        return new_visitor_id()

    def close(self) -> None:
        """Closes the client: later calls raise `RuntimeError`. Closing twice is safe.

        A call in another thread stops at its next attempt or retry wait. It drops the in-memory
        cache (a custom store is left as it is) and releases an HTTP client it built itself.
        """
        self._driver.close()
        if self._owns_store and isinstance(self._driver.store, MemoryCache):
            self._driver.store.clear()

    def __enter__(self) -> EntitlerServer:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()

    def __repr__(self) -> str:
        return f"EntitlerServer(base_url={self._driver.engine.base_url!r}, kind='server')"


class EntitlerClient:
    """The in-app client, for desktop apps and other code running on the customer's device.

    Build it with `token=` (a customer token your server minted, or a function that fetches one),
    with `key=` and `identity_token=` (a publishable key and your sign-in provider's token), or
    with `key=` alone for signed-out pricing. A secret server key never goes into an app: anyone
    can read it out of the app and act on every customer, so the client refuses one.

    Keep one client per signed-in person where the app keeps its session state, and close it at
    sign-out, so nothing kept for one person is ever answered to another.

    Example:
        ```python
        client = EntitlerClient(token=fetch_token_from_my_server)
        if client.me.is_entitled(features.EXPORT_PDF, default=False):
            show_export()
        client.close()
        ```
    """

    @overload
    def __init__(
        self,
        *,
        token: str | TokenProvider,
        visitor: str | None = None,
        base_url: str = "https://api.entitler.dev",
        timeout: float = 10.0,
        max_retries: int = 2,
        max_retry_delay: float = 10.0,
        cache: MemoryCache | int | None = 1000,
        stale_for: float = 86400.0,
        on_error: OnError | None = None,
        http_client: httpx.Client | None = None,
    ) -> None: ...
    @overload
    def __init__(
        self,
        *,
        key: str,
        identity_token: str | TokenProvider,
        visitor: str | None = None,
        base_url: str = "https://api.entitler.dev",
        timeout: float = 10.0,
        max_retries: int = 2,
        max_retry_delay: float = 10.0,
        cache: MemoryCache | int | None = 1000,
        stale_for: float = 86400.0,
        on_error: OnError | None = None,
        http_client: httpx.Client | None = None,
    ) -> None: ...
    @overload
    def __init__(
        self,
        *,
        key: str,
        visitor: str | None = None,
        base_url: str = "https://api.entitler.dev",
        timeout: float = 10.0,
        max_retries: int = 2,
        max_retry_delay: float = 10.0,
        cache: MemoryCache | int | None = 1000,
        stale_for: float = 86400.0,
        on_error: OnError | None = None,
        http_client: httpx.Client | None = None,
    ) -> None: ...
    def __init__(
        self,
        *,
        token: str | TokenProvider | None = None,
        key: str | None = None,
        identity_token: str | TokenProvider | None = None,
        visitor: str | None = None,
        base_url: str = "https://api.entitler.dev",
        timeout: float = 10.0,
        max_retries: int = 2,
        max_retry_delay: float = 10.0,
        cache: MemoryCache | int | None = 1000,
        stale_for: float = 86400.0,
        on_error: OnError | None = None,
        http_client: httpx.Client | None = None,
    ) -> None:
        """Creates an in-app client.

        Args:
            token: A customer token from `ServerCustomer.token()`, or a function that fetches a
                fresh one from your server. The client asks it again shortly before the token
                expires, and once after a `401`.
            key: A publishable key (`ent_pk_…`), with `identity_token` or alone.
            identity_token: The identity token your sign-in provider issued, or a function that
                answers a fresh one.
            visitor: The visitor id to send on every request; a new one for the client's lifetime
                when left out. A desktop app keeps `client.visitor` and passes it back here.
            base_url: The API's base URL.
            timeout: Each attempt's deadline in seconds.
            max_retries: How many times to retry after the first attempt.
            max_retry_delay: The longest `Retry-After`, in seconds, the client waits for.
            cache: The in-memory answer cache's size, a `MemoryCache`, or `None` for none. Only
                snapshots survive a relaunch.
            stale_for: How long, in seconds, a kept answer may stand in while Entitler is
                unreachable.
            on_error: Called with each error a fallback absorbed.
            http_client: An `httpx.Client` to send requests with. The client never closes it.

        Raises:
            TypeError: the credentials are not one of the three combinations.
            ValueError: a credential is blank, the key is a secret key, or an option is out of
                range.
        """
        kind, publishable, given = in_app_credentials(token, key, identity_token, cache)
        settings = Settings(base_url, timeout, max_retries, max_retry_delay, stale_for, on_error)
        store = settings.store(cache, custom=False)
        self._owns_store = store is not None
        self._visitor = settings.visitor(visitor)
        sent = {"Entitler-Visitor": self._visitor}
        engine = settings.engine(cached=store is not None, extra_headers=lambda: sent)
        source = None if given is None else SyncToken(given, settings.blank_token(kind))
        credentials = SyncCredentials(kind, publishable, source)
        self._driver = SyncDriver(engine, credentials, store, http_client)
        self._me = (
            None if kind == "publishable" else Customer(CustomerOps(engine, None), self._driver)
        )

    @property
    def kind(self) -> Literal["token", "identity", "publishable"]:
        """How the client was built: `token`, `identity` or `publishable`."""
        kind = self._driver.credentials.kind
        return "token" if kind == "token" else "identity" if kind == "identity" else "publishable"

    @property
    def visitor(self) -> str:
        """The visitor id sent on every request."""
        return self._visitor

    @property
    def me(self) -> Customer:
        """The signed-in customer. Makes no request.

        Raises:
            TypeError: the client was built from a publishable key alone.
        """
        if self._me is None:
            raise TypeError(SIGN_IN)
        return self._me

    def register(
        self, *, idempotency_key: str | None = None, timeout: float = CLIENT_TIMEOUT
    ) -> RegisteredCustomer:
        """Registers the signed-in person as a customer; call it after each sign-in.

        An existing customer is answered without being created again. It suits identity clients:
        a customer token client's `register()` is sent, and the API refuses it.
        `403 registration_closed` means the sign-in provider does not let people register
        themselves, and `403 scope_required` that the key lacks `customers:register`.
        """
        me = self.me
        return self._driver.run(me._ops.register_me(idempotency_key, timeout))

    def scopes(self, *, timeout: float = CLIENT_TIMEOUT) -> Scopes:
        """The scopes the credential holds and, for an identity client, `registration`."""
        if self._me is None:
            raise TypeError(SIGN_IN)
        return self._driver.run(
            scopes(self._driver.engine, timeout, identity=self.kind == "identity")
        )

    def pricing(
        self,
        *,
        visitor: str | None = None,
        revalidate: bool = False,
        timeout: float = CLIENT_TIMEOUT,
    ) -> Pricing:
        """The pricing on sale, signed out, with the client's visitor, through the answer cache.

        Raises:
            TypeError: the client is signed in; read `me.pricing()` instead.
        """
        if self._me is not None:
            raise TypeError("Read the signed-in customer's pricing with me.pricing().")
        return self._driver.run(pricing(self._driver.engine, visitor, revalidate, timeout))

    def snapshot_keys(self, *, timeout: float = CLIENT_TIMEOUT) -> SnapshotKeys:
        """The public keys that verify snapshots. Sends no credential."""
        return self._driver.run(snapshot_keys(self._driver.engine, timeout))[0]

    verify_snapshot = EntitlerServer.verify_snapshot

    def close(self) -> None:
        """Closes the client at sign-out. Later calls raise `RuntimeError`; closing twice is safe.

        It drops the in-memory cache, and a call in another thread stops at its next attempt or
        retry wait.
        """
        self._driver.close()
        if isinstance(self._driver.store, MemoryCache):
            self._driver.store.clear()

    def __enter__(self) -> EntitlerClient:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()

    def __repr__(self) -> str:
        return f"EntitlerClient(base_url={self._driver.engine.base_url!r}, kind={self.kind!r})"


from ._settings import OnError  # noqa: E402 - the alias the signatures name
