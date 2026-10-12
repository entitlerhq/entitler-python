"""Feature constants, typed by kind."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Generic, TypeVar

from ._models import FeatureType


class Boolean:
    """Marks an on/off feature's constant: `Feature[Boolean]`."""


class Config:
    """Marks a config feature's constant: `Feature[Config]`."""


class Metered:
    """Marks a metered feature's constant: `Feature[Metered]`, which usage methods take."""


class Group:
    """Marks a group feature's constant: `Feature[Group]`."""


KindT = TypeVar("KindT", covariant=True)


@dataclass(frozen=True, slots=True)
class Feature(Generic[KindT]):
    """A feature constant: its key, its kind and, for a group, its leaf members.

    The generator writes these; declare one by hand with `Feature.boolean`, `Feature.config`,
    `Feature.metered` or `Feature.group`. The kind types the answers: `check()` of a
    `Feature[Metered]` answers a `MeteredCheck`, and usage methods take only `Feature[Metered]`.

    Example:
        ```python
        AI_CREDITS = Feature.metered("ai_credits")
        customer.record_usage(AI_CREDITS, 3, idempotency_key=job.id)
        ```
    """

    key: str
    """The feature's key."""
    type: FeatureType | str
    """The feature's kind."""
    includes: tuple[str, ...] = ()
    """A group's leaf member keys, for documentation."""

    @staticmethod
    def boolean(key: str) -> Feature[Boolean]:
        """Declares an on/off feature."""
        return Feature(key, FeatureType.BOOLEAN)

    @staticmethod
    def config(key: str) -> Feature[Config]:
        """Declares a config feature, whose value is an amount or unlimited."""
        return Feature(key, FeatureType.CONFIG)

    @staticmethod
    def metered(key: str) -> Feature[Metered]:
        """Declares a metered feature, which records usage."""
        return Feature(key, FeatureType.METERED)

    @staticmethod
    def group(key: str, includes: tuple[str, ...] = ()) -> Feature[Group]:
        """Declares a group feature, with its leaf members for documentation."""
        return Feature(key, FeatureType.GROUP, tuple(includes))
