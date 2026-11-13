"""Connector state normalisation, reproducing the Ecobox web map's own logic."""

AVAILABLE = "available"
CHARGING = "charging"
UNAVAILABLE = "unavailable"
FAULTED = "faulted"
OFFLINE = "offline"
MAINTENANCE = "maintenance"

ALL_STATES = (AVAILABLE, CHARGING, UNAVAILABLE, FAULTED, OFFLINE, MAINTENANCE)

_BY_AVAILABILITY = {
    1: AVAILABLE,
    2: CHARGING,
    3: UNAVAILABLE,
    4: FAULTED,
    5: OFFLINE,
    6: MAINTENANCE,
}

# connectorChargingState values the web map falls back on.
_CHARGING_STATE_OFFLINE = 1
_CHARGING_STATE_AVAILABLE = 3


def normalise(availability: int | None, is_under_maintenance: bool | None,
              charging_state: int | None) -> str:
    """Map the raw status fields to one normalised state string."""
    if availability in _BY_AVAILABILITY:
        return _BY_AVAILABILITY[availability]
    if is_under_maintenance:
        return MAINTENANCE
    if charging_state == _CHARGING_STATE_AVAILABLE:
        return AVAILABLE
    if charging_state == _CHARGING_STATE_OFFLINE:
        return OFFLINE
    return CHARGING
