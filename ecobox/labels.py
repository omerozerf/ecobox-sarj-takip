"""Short socket names: the catalog's labels without the site name they all repeat, and the
location words a person would type to pick a group of them."""

import re
from typing import Callable

from ecobox.records import CatalogEntry

_SEPARATORS = re.compile(r"[\s\-#]+")
_TRAILING_NOISE = "ac"


def _tokens(label: str) -> list[str]:
    return [t for t in _SEPARATORS.split(label) if t]


def _shared_prefix_length(token_lists: list[list[str]]) -> int:
    """Leading tokens every label has in common (case-insensitive), always leaving one."""
    if len(token_lists) < 2:
        return 0
    folded = [[t.casefold() for t in tokens] for tokens in token_lists]
    limit = min(len(tokens) for tokens in folded) - 1
    length = 0
    while length < limit and len({tokens[length] for tokens in folded}) == 1:
        length += 1
    return length


def _shorten(tokens: list[str], prefix_length: int) -> str:
    rest = tokens[prefix_length:]
    if len(rest) > 1 and rest[-1].casefold() == _TRAILING_NOISE:
        rest = rest[:-1]
    return " ".join(rest)


class ShortNames:
    """Short names for one catalog; colliding ones keep their full label."""

    def __init__(self, catalog: list[CatalogEntry]):
        token_lists = [_tokens(e.label) for e in catalog]
        prefix_length = _shared_prefix_length(token_lists)
        shorts = [_shorten(tokens, prefix_length) for tokens in token_lists]
        taken = {s for s in shorts if shorts.count(s) == 1}
        self._by_connector = {
            e.connector_id: short if short in taken else e.label
            for e, short in zip(catalog, shorts)}

    def of(self, entry: CatalogEntry) -> str:
        return self._by_connector[entry.connector_id]


def _group_words(short: str) -> tuple[str, ...]:
    """"3. Arge Otopark 1" -> ("arge", "otopark"): the group a socket belongs to, without its
    number or a leading ordinal."""
    words = [w for w in short.split() if not w.rstrip(".").isdigit()]
    return tuple(w.replace("I", "ı").replace("İ", "i").lower() for w in words)


def location_words(short_names: list[str],
                   selects: Callable[[str], frozenset[str]]) -> list[str]:
    """Short location terms derived from socket short names, e.g. ego · f blok · otopark.

    `selects(term)` is the set of sockets a term picks. A group that picks exactly what a
    longer group starting with it picks is redundant ("f" next to "f blok"); a group that
    starts with another kept group keeps only its own words ("arge otopark" next to "arge"
    becomes "otopark"). Terms that pick nothing are left out.
    """
    groups = {g for g in map(_group_words, short_names) if g}
    kept = {g for g in groups
            if not any(len(o) > len(g) and o[:len(g)] == g
                       and selects(" ".join(o)) == selects(" ".join(g)) for o in groups)}
    terms = set()
    for group in kept:
        prefix = max((o for o in kept if len(o) < len(group) and group[:len(o)] == o),
                     key=len, default=())
        term = " ".join(group[len(prefix):])
        if selects(term):
            terms.add(term)
    return sorted(terms)
