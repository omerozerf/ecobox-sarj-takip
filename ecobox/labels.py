"""Short socket names: the catalog's labels without the site name they all repeat."""

import re

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
