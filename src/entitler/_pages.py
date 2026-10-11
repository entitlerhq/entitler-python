"""Paged lists, each page requested only when iteration reaches it."""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from typing import Generic, TypeVar

from ._models import CustomerPage, CustomerSummary, Page, UsageEvent

T = TypeVar("T")


class Pager(Generic[T]):
    """Every item of a paged list, fetching each page only when iteration reaches it.

    Example:
        ```python
        for customer in server.customers.list(q="acme"):
            print(customer.external_id)
        ```
    """

    def __init__(self, fetch: Callable[[str | None], Page[T]], start: str | None = None) -> None:
        self._fetch = fetch
        self._start = start
        self._first: Page[T] | None = None

    def pages(self) -> Iterator[Page[T]]:
        """Each page in turn, from the first or the cursor the list started at."""
        page = self._first if self._first is not None else self._fetch(self._start)
        yield page
        while page.next is not None:
            page = self._fetch(page.next)
            yield page

    def __iter__(self) -> Iterator[T]:
        for page in self.pages():
            yield from page.items


class AsyncPager(Generic[T]):
    """Every item of a paged list, for `async for`, fetching each page only when reached."""

    def __init__(
        self, fetch: Callable[[str | None], Awaitable[Page[T]]], start: str | None = None
    ) -> None:
        self._fetch = fetch
        self._start = start
        self._first: Page[T] | None = None

    async def pages(self) -> AsyncIterator[Page[T]]:
        """Each page in turn, from the first or the cursor the list started at."""
        page = self._first if self._first is not None else await self._fetch(self._start)
        yield page
        while page.next is not None:
            page = await self._fetch(page.next)
            yield page

    async def __aiter__(self) -> AsyncIterator[T]:
        async for page in self.pages():
            for item in page.items:
                yield item


class CustomerPager(Pager[CustomerSummary]):
    """The customer list: each page also carries the project's customer count and limit."""

    def pages(self) -> Iterator[CustomerPage]:
        """Each page in turn, with `used` and `limit`."""
        for page in super().pages():
            assert isinstance(page, CustomerPage)  # noqa: S101 - the fetch answers customer pages
            yield page


class AsyncCustomerPager(AsyncPager[CustomerSummary]):
    """The customer list, for `async for`: each page also carries `used` and `limit`."""

    async def pages(self) -> AsyncIterator[CustomerPage]:
        """Each page in turn, with `used` and `limit`."""
        async for page in super().pages():
            assert isinstance(page, CustomerPage)  # noqa: S101 - the fetch answers customer pages
            yield page


class UsageLog(Pager[UsageEvent]):
    """A usage log: its first page's `items` and `next`, and every entry on iteration."""

    def __init__(self, first: Page[UsageEvent], fetch: Callable[[str], Page[UsageEvent]]) -> None:
        super().__init__(lambda cursor: fetch(cursor or ""))
        self._first = first
        self.items = first.items
        """The first page's entries."""
        self.next = first.next
        """The cursor of the page after the first, or `None`."""


class AsyncUsageLog(AsyncPager[UsageEvent]):
    """A usage log, for `async for`: its first page's `items` and `next`, and every entry."""

    def __init__(
        self, first: Page[UsageEvent], fetch: Callable[[str], Awaitable[Page[UsageEvent]]]
    ) -> None:
        super().__init__(lambda cursor: fetch(cursor or ""))
        self._first = first
        self.items = first.items
        """The first page's entries."""
        self.next = first.next
        """The cursor of the page after the first, or `None`."""
