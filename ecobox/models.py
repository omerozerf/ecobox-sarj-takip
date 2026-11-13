"""Domain records parsed from the Ecobox map API payloads."""

import json
from dataclasses import dataclass, field
from typing import Any

AC_TYPE = 1
DC_TYPE = 2


@dataclass(frozen=True)
class Tariff:
    tariff_id: str
    energy_price: float
    service_price: float | None
    initial_price: float | None
    currency: str
    vat: float | None
    effective_date: str | None

    @classmethod
    def from_api(cls, raw: dict[str, Any]) -> "Tariff":
        return cls(
            tariff_id=raw["id"],
            energy_price=float(raw["energyPrice"]),
            service_price=raw.get("servicePrice"),
            initial_price=raw.get("initialPrice"),
            currency=raw["currency"],
            vat=raw.get("vat"),
            effective_date=raw.get("effectiveDate"),
        )


@dataclass(frozen=True)
class ConnectorInfo:
    id: str
    charge_point_id: str
    connector_order: int
    type: int
    power_kw: float
    tariff: Tariff | None
    has_barrier: bool
    maintenance_title: str | None
    maintenance_end_at: str | None
    socket_code: str | None = None
    is_under_maintenance: bool | None = None
    reservation_status: str | None = None
    """connectorReservationStatus as JSON text; None when the API gives none."""
    nearby_reservations: str | None = None
    """nearByConnectorReservations as JSON text; None when the list is empty or missing."""

    @property
    def is_ac(self) -> bool:
        return self.type == AC_TYPE

    @classmethod
    def from_api(cls, raw: dict[str, Any], charge_point_id: str) -> "ConnectorInfo":
        tariff = raw.get("tariff")
        return cls(
            id=raw["id"],
            charge_point_id=charge_point_id,
            connector_order=raw["connectorOrder"],
            type=raw["type"],
            power_kw=float(raw["power"]),
            tariff=Tariff.from_api(tariff) if tariff else None,
            has_barrier=bool(raw.get("hasBarrier")),
            maintenance_title=raw.get("maintenanceTitle"),
            maintenance_end_at=raw.get("maintenanceEndAt"),
            socket_code=raw.get("socketCode"),
            is_under_maintenance=raw.get("isUnderMaintenance"),
            reservation_status=_json_or_none(raw.get("connectorReservationStatus")),
            nearby_reservations=_json_or_none(raw.get("nearByConnectorReservations")),
        )


def _json_or_none(value: Any) -> str | None:
    """JSON text of a value worth keeping; None, empty lists and empty dicts become None."""
    if value is None or value == [] or value == {}:
        return None
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


@dataclass(frozen=True)
class ChargePoint:
    id: str
    display_name: str
    location_id: str
    location_name: str
    address: str | None
    latitude: float | None
    longitude: float | None
    is_public: bool
    connectors: tuple[ConnectorInfo, ...]
    raw: dict[str, Any] = field(compare=False, repr=False)
    is_device_in_working_hours: bool | None = None
    supports_auto_charge: bool | None = None
    has_available_connectors: bool | None = None

    @classmethod
    def from_api(cls, raw: dict[str, Any]) -> "ChargePoint":
        cp_id = raw["id"]
        return cls(
            id=cp_id,
            display_name=raw["displayName"].strip(),
            location_id=raw["locationId"],
            location_name=raw["locationName"].strip(),
            address=raw.get("address"),
            latitude=raw.get("latitude"),
            longitude=raw.get("longitude"),
            is_public=bool(raw.get("isPublic")),
            connectors=tuple(ConnectorInfo.from_api(c, cp_id) for c in raw["connectors"]),
            raw=raw,
            is_device_in_working_hours=raw.get("isDeviceInWorkingHours"),
            supports_auto_charge=raw.get("supportsAutoCharge"),
            has_available_connectors=raw.get("hasAvailableConnectors"),
        )


# Status-row keys the recorder stores in their own columns; anything else the API starts
# sending is kept verbatim in samples.extra_json.
_KNOWN_STATUS_KEYS = frozenset({
    "chargePointId", "connectorId", "connectorOrder", "availability", "connectorChargingState",
    "connectorChargingStateDisplayName", "isUnderMaintenance"})


@dataclass(frozen=True)
class ConnectorStatus:
    """One row of /api/app/connector/status, raw fields kept as received."""

    charge_point_id: str
    connector_id: str
    connector_order: int | None
    availability: int | None
    charging_state: int | None
    charging_state_name: str | None
    is_under_maintenance: bool | None
    extra_json: str | None = field(default=None, compare=False)
    """Fields beyond the known ones, as JSON text; None while the API sends nothing new."""

    @classmethod
    def from_api(cls, raw: dict[str, Any]) -> "ConnectorStatus":
        return cls(
            charge_point_id=raw["chargePointId"],
            connector_id=raw["connectorId"],
            connector_order=raw.get("connectorOrder"),
            availability=raw.get("availability"),
            charging_state=raw.get("connectorChargingState"),
            charging_state_name=raw.get("connectorChargingStateDisplayName"),
            is_under_maintenance=raw.get("isUnderMaintenance"),
            extra_json=_json_or_none({k: v for k, v in raw.items()
                                      if k not in _KNOWN_STATUS_KEYS}),
        )


def parse_station_list(payload: dict[str, Any]) -> list[ChargePoint]:
    return [ChargePoint.from_api(item) for item in payload["result"]["items"]]


def parse_status(payload: dict[str, Any]) -> list[ConnectorStatus]:
    return [ConnectorStatus.from_api(row) for row in payload["result"]]
