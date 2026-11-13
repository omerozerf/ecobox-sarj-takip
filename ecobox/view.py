"""The display scope: the part of the recorded catalog the bot and the default analysis show.

The recorder samples every Ecobox site; the chat commands, notifications and short socket
names stay on the display scope (Hacettepe by default) so nothing else leaks into them.
"""

from ecobox.labels import ShortNames
from ecobox.records import CatalogEntry, CurrentStatus
from ecobox.scope import ScopeFilter
from ecobox.stats import Selection
from ecobox.storage import Storage


class DisplayScope:
    def __init__(self, storage: Storage, terms: tuple[str, ...]):
        self._storage = storage
        self._terms = terms
        self._filter = ScopeFilter(terms)

    @property
    def label(self) -> str:
        """How the default analysis names the scope, e.g. "Hacettepe"."""
        if not self._terms:
            return "Ecobox"
        return ", ".join(_title(t) for t in self._terms)

    def catalog(self) -> list[CatalogEntry]:
        """Every recorded connector (AC and DC) inside the display scope."""
        return [e for e in self._storage.catalog()
                if self._filter.matches_names(e.display_name, e.location_name)]

    def names(self) -> ShortNames:
        """Short names computed over the display scope, never over every site."""
        return ShortNames(self.catalog())

    def current_status(self) -> list[CurrentStatus]:
        return self._storage.current_status({e.connector_id for e in self.catalog()})

    def selection(self, location_term: str | None = None) -> Selection:
        """AC connectors in the display scope, optionally narrowed by a location term."""
        return Selection(location_term=location_term, scope_terms=self._terms)


def _title(term: str) -> str:
    first = "İ" if term[0] == "i" else term[0].upper()
    return first + term[1:]
