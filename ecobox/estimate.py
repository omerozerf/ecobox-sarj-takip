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


@dataclass(frozen=True)
class EnergyCurve:
    """Station energy per displayed battery percent, with a costlier top band.

    Measured on 2026-11-13 (32 % -> 100 %, 66.26 kWh): 0.94 kWh/% up to 84 %, 1.10 kWh/% above,
    at constant power. Where inside 84-100 the rate really changes was not observed.
    """
    kwh_per_percent: float
    top_band_start_pct: float
    top_band_kwh_per_percent: float

    @classmethod
    def flat(cls, kwh_per_percent: float) -> "EnergyCurve":
        return cls(kwh_per_percent, 100.0, kwh_per_percent)

    def energy_kwh(self, start_pct: float, target_pct: float) -> float:
        knee = self.top_band_start_pct
        below = min(target_pct, knee) - min(start_pct, knee)
        above = max(target_pct, knee) - max(start_pct, knee)
        return below * self.kwh_per_percent + above * self.top_band_kwh_per_percent


class ChargeEstimator:
    def __init__(self, car_charge_kw: float, curve: EnergyCurve, price_includes_vat: bool):
        self._car_kw = car_charge_kw
        self._curve = curve
        self._price_includes_vat = price_includes_vat

    def estimate(self, start_pct: float, target_pct: float, now: datetime,
                 connector_power_kw: float | None, tariff: Tariff | None) -> ChargeEstimate:
        if not 0 <= start_pct < target_pct <= 100:
            raise ValueError("need 0 <= start < target <= 100")
        effective_kw = self._car_kw if connector_power_kw is None else min(
            self._car_kw, connector_power_kw)
        energy = self._curve.energy_kwh(start_pct, target_pct)
        duration = timedelta(hours=energy / effective_kw)
        cost = currency = service = None
        if tariff is not None:
            price = tariff.energy_price
            if not self._price_includes_vat and tariff.vat:
                price *= 1 + tariff.vat / 100
            cost, currency, service = energy * price, tariff.currency, tariff.service_price
        return ChargeEstimate(start_pct, target_pct, energy, effective_kw, duration,
                              now + duration, cost, currency, service)
