"""The base entity of the Kodi sensors."""

from homeassistant.const import CONF_NAME
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import EntityDescription
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import KodiStreamCoordinator


class KodiCoordinatorEntity(CoordinatorEntity[KodiStreamCoordinator]):
    """An entity of the Kodi device that the coordinator updates."""

    _attr_has_entity_name = True

    def __init__(
        self, coordinator: KodiStreamCoordinator, description: EntityDescription
    ) -> None:
        """Initialize the entity."""
        super().__init__(coordinator)
        self.entity_description = description
        entry = coordinator.config_entry
        # The same device as the media player
        uid = entry.unique_id or entry.entry_id
        self._attr_unique_id = f"{uid}_{description.key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, uid)},
            manufacturer="Kodi",
            name=entry.data[CONF_NAME] or entry.title,
        )
