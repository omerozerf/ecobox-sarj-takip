"""Charge time and cost estimate for the owner's car on an AC socket."""

from dataclasses import dataclass
from datetime import datetime, timedelta

from ecobox.idle_fee import IdleDeadline, IdleFeePolicy
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
    idle: IdleDeadline | None = None
    """When the car has to leave the socket; None while no idle-fee policy is configured."""


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

    def _split(self, start_pct: float, target_pct: float) -> tuple[float, float]:
        """Percent points below and above the knee."""
        knee = self.top_band_start_pct
        return (min(target_pct, knee) - min(start_pct, knee),
                max(target_pct, knee) - max(start_pct, knee))

    def energy_kwh(self, start_pct: float, target_pct: float) -> float:
        below, above = self._split(start_pct, target_pct)
        return below * self.kwh_per_percent + above * self.top_band_kwh_per_percent

    def charge_hours(self, start_pct: float, target_pct: float, power_kw: float,
                     top_band_time_factor: float) -> float:
        """Time to charge, kept apart from the energy: a top-band percent takes
        `top_band_time_factor` times as long as one below the knee. The energy the meter
        counted there (1.10 kWh/% on 2026-11-13) overstated the time on 2026-11-24, when the
        last 16 points went nearly as fast as the rest."""
        below, above = self._split(start_pct, target_pct)
        return (below + above * top_band_time_factor) * self.kwh_per_percent / power_kw


class ChargeEstimator:
    def __init__(self, car_charge_kw: float, curve: EnergyCurve, price_includes_vat: bool,
                 idle_fee: IdleFeePolicy | None = None, top_band_time_factor: float | None = None):
        self._car_kw = car_charge_kw
        self._curve = curve
        # Without a measured factor the time follows the energy, as it did before.
        self._top_time = (curve.top_band_kwh_per_percent / curve.kwh_per_percent
                          if top_band_time_factor is None else top_band_time_factor)
        self._price_includes_vat = price_includes_vat
        self._idle_fee = idle_fee

    def estimate(self, start_pct: float, target_pct: float, now: datetime,
                 connector_power_kw: float | None, tariff: Tariff | None) -> ChargeEstimate:
        if not 0 <= start_pct < target_pct <= 100:
            raise ValueError("need 0 <= start < target <= 100")
        effective_kw = self._car_kw if connector_power_kw is None else min(
            self._car_kw, connector_power_kw)
        energy = self._curve.energy_kwh(start_pct, target_pct)
        duration = timedelta(hours=self._curve.charge_hours(start_pct, target_pct, effective_kw,
                                                            self._top_time))
        cost = currency = service = None
        if tariff is not None:
            price = tariff.energy_price
            if not self._price_includes_vat and tariff.vat:
                price *= 1 + tariff.vat / 100
            cost, currency, service = energy * price, tariff.currency, tariff.service_price
        finish_at = now + duration
        idle = None if self._idle_fee is None else self._idle_fee.deadline(finish_at)
        return ChargeEstimate(start_pct, target_pct, energy, effective_kw, duration,
                              finish_at, cost, currency, service, idle)
