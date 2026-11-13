"""The display scope: the part of the recorded catalog the bot and the default analysis show.

The recorder samples every Ecobox site; the chat commands, notifications and short socket
names stay on the display scope (Hacettepe by default) so nothing else leaks into them.
"""

import re
from dataclasses import replace

from ecobox.labels import ShortNames, location_words
from ecobox.records import CatalogEntry, CurrentStatus
from ecobox.scope import ScopeFilter, fold
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

    def statuses(self, location_term: str | None = None) -> list[CurrentStatus]:
        """Current status of the scope's sockets (AC and DC) matching a location term."""
        ids = {e.connector_id for e in replace(self.selection(location_term), ac_only=False)
               .apply(self.catalog())}
        return [s for s in self.current_status() if s.entry.connector_id in ids]

    def location_terms(self) -> list[str]:
        """Short words that pick a group of AC sockets: what to suggest, and what free text
        is recognised as."""
        catalog = self.catalog()
        names = ShortNames(catalog)
        ac = [e for e in catalog if e.is_ac]

        def selects(term: str) -> frozenset[str]:
            return frozenset(e.connector_id for e in self.selection(term).apply(ac))
        return location_words([names.of(e) for e in ac], selects)

    def recognise(self, text: str) -> str | None:
        """The location term a free-text message names as a whole word ("ego boş mu" ->
        "ego"), the longest one when several do; None when it names none."""
        folded = fold(text)
        found = [t for t in self.location_terms()
                 if re.search(rf"(?<!\w){re.escape(fold(t))}(?!\w)", folded)]
        return max(found, key=len, default=None)


def _title(term: str) -> str:
    first = "İ" if term[0] == "i" else term[0].upper()
    return first + term[1:]
