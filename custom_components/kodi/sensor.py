"""Sensors of the streams Kodi plays."""

from collections.abc import Callable
from dataclasses import dataclass
from typing import override

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.typing import StateType

from .connection import KodiConfigEntry
from .coordinator import KodiStreamCoordinator, KodiStreamData
from .entity import KodiCoordinatorEntity

PARALLEL_UPDATES = 0

# Kodi reports these with CStreamDetails::HdrTypeToString, and SDR as empty text
DYNAMIC_RANGES = ["sdr", "hdr10", "hdr10plus", "dolbyvision", "hlg"]


def _dynamic_range(data: KodiStreamData) -> str | None:
    """Return the dynamic range of the video Kodi plays, SDR without video."""
    if data.video is None:
        return "sdr"
    hdr_type = data.video["infolabels"]["VideoPlayer.HdrType"] or "sdr"
    # A newer Kodi could report a type that is not an option of the sensor
    return hdr_type if hdr_type in DYNAMIC_RANGES else None


@dataclass(frozen=True, kw_only=True)
class KodiSensorEntityDescription(SensorEntityDescription):
    """Describes a Kodi sensor."""

    value_fn: Callable[[KodiStreamData], StateType]


SENSORS: tuple[KodiSensorEntityDescription, ...] = (
    KodiSensorEntityDescription(
        key="dynamic_range",
        translation_key="dynamic_range",
        device_class=SensorDeviceClass.ENUM,
        options=DYNAMIC_RANGES,
        value_fn=_dynamic_range,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: KodiConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the Kodi sensors."""
    coordinator = KodiStreamCoordinator(hass, config_entry)
    coordinator.async_start()
    async_add_entities(
        KodiSensorEntity(coordinator, description) for description in SENSORS
    )


class KodiSensorEntity(KodiCoordinatorEntity, SensorEntity):
    """A sensor of the streams Kodi plays."""

    entity_description: KodiSensorEntityDescription

    @property
    @override
    def native_value(self) -> StateType:
        """Return the state of the sensor, None while Kodi is off."""
        if (data := self.coordinator.data) is None:
            return None
        return self.entity_description.value_fn(data)
