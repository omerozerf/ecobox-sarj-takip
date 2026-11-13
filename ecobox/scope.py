"""Which charge points are recorded, and Turkish-tolerant text matching."""

from ecobox.models import ChargePoint


def fold(text: str) -> str:
    """Case-fold for matching, treating Turkish dotted/dotless i as plain i."""
    return (text.replace("İ", "i").replace("I", "i").replace("ı", "i")
            .casefold().strip())


def matches_terms(text: str, terms: tuple[str, ...]) -> bool:
    folded = fold(text)
    return any(term in folded for term in terms)


class ScopeFilter:
    """Keeps charge points whose displayName or locationName contains any term.

    An empty term list keeps everything.
    """

    def __init__(self, terms: tuple[str, ...]):
        self._terms = tuple(fold(t) for t in terms if t.strip())

    @property
    def terms(self) -> tuple[str, ...]:
        return self._terms

    def matches(self, charge_point: ChargePoint) -> bool:
        if not self._terms:
            return True
        return matches_terms(f"{charge_point.display_name} {charge_point.location_name}",
                             self._terms)

    def select(self, charge_points: list[ChargePoint]) -> list[ChargePoint]:
        return [cp for cp in charge_points if self.matches(cp)]
