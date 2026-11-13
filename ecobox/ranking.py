"""One order for "which socket is most often free", shared by `istatistik` and `analiz`.

Both replies rank sockets the same way so that the same data never reads as two answers:
by free share, highest first; equal shares by short name.
"""

from typing import Callable, Iterable, TypeVar

T = TypeVar("T")


def by_free_share(items: Iterable[T], share: Callable[[T], float],
                  name: Callable[[T], str]) -> list[T]:
    return sorted(items, key=lambda item: (-share(item), name(item)))
