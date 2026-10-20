"""Base entity for Smashrun."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import SmashrunCoordinator


class SmashrunEntity(CoordinatorEntity[SmashrunCoordinator]):
    """All entities of an account share one service device."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: SmashrunCoordinator, key: str) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.uid}_{key}"
        username = coordinator.user.get("username")
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, coordinator.uid)},
            name=f"Smashrun {username}" if username else "Smashrun",
            manufacturer="Smashrun",
            entry_type=DeviceEntryType.SERVICE,
            configuration_url=f"https://smashrun.com/{username}" if username else None,
        )

    def run_url(self, run_id: str) -> str | None:
        username = self.coordinator.user.get("username")
        if not username or not run_id:
            return None
        return f"https://smashrun.com/{username}/run/{run_id}"
