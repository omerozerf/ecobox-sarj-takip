"""Charge time and cost estimate for the owner's car on an AC socket."""

from dataclasses import dataclass
from datetime import datetime, timedelta

from ecobox.models import Tariff


@dataclass(frozen=True)
class ChargeEstimate:
    start_pct: float
    target_pct: float
    energy_kwh: float
    effective_kw: float
    duration: timedelta
    finish_at: datetime
    cost: float | None
    currency: str | None
    service_price: float | None
    """Reported separately: the API does not say what unit servicePrice is in."""


class ChargeEstimator:
    def __init__(self, car_charge_kw: float, kwh_per_percent: float, price_includes_vat: bool):
        self._car_kw = car_charge_kw
        self._kwh_per_percent = kwh_per_percent
        self._price_includes_vat = price_includes_vat

    def estimate(self, start_pct: float, target_pct: float, now: datetime,
                 connector_power_kw: float | None, tariff: Tariff | None) -> ChargeEstimate:
        if not 0 <= start_pct < target_pct <= 100:
            raise ValueError("need 0 <= start < target <= 100")
        effective_kw = self._car_kw if connector_power_kw is None else min(
            self._car_kw, connector_power_kw)
        energy = (target_pct - start_pct) * self._kwh_per_percent
        duration = timedelta(hours=energy / effective_kw)
        cost = currency = service = None
        if tariff is not None:
            price = tariff.energy_price
            if not self._price_includes_vat and tariff.vat:
                price *= 1 + tariff.vat / 100
            cost, currency, service = energy * price, tariff.currency, tariff.service_price
        return ChargeEstimate(start_pct, target_pct, energy, effective_kw, duration,
                              now + duration, cost, currency, service)
