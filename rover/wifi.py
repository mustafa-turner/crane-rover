from __future__ import annotations

import logging
import shutil
import subprocess
from dataclasses import dataclass


CONNECTION_PREFIX = "crane-rover-wifi"
DEFAULT_INTERFACE = "wlan0"
SLOT_COUNT = 4


@dataclass
class WifiSlot:
    index: int
    ssid: str
    password: str


def apply_preferred_wifi(wifi_cfg: dict) -> None:
    if not wifi_cfg.get("enabled", False):
        return

    if shutil.which("nmcli") is None:
        logging.warning("Wi-Fi auto-connect is enabled but nmcli is not installed")
        return

    interface = str(wifi_cfg.get("interface", DEFAULT_INTERFACE)).strip() or DEFAULT_INTERFACE
    slots = _configured_slots(wifi_cfg)
    if not slots:
        logging.info("Wi-Fi auto-connect enabled but no SSIDs are configured")
        return

    visible_ssids = _scan_visible_ssids(interface)
    if not visible_ssids:
        logging.warning("Wi-Fi scan returned no visible SSIDs on %s", interface)
        return

    current_ssid = _current_ssid(interface)
    for slot in slots:
        if slot.ssid not in visible_ssids:
            continue
        if current_ssid == slot.ssid:
            logging.info("Wi-Fi already connected to preferred SSID %s", slot.ssid)
            return
        if _connect_slot(interface, slot):
            return

    logging.warning("None of the configured Wi-Fi SSIDs are currently available on %s", interface)


def _configured_slots(wifi_cfg: dict) -> list[WifiSlot]:
    slots: list[WifiSlot] = []
    for index in range(1, SLOT_COUNT + 1):
        ssid = str(wifi_cfg.get(f"network{index}Ssid", "")).strip()
        password = str(wifi_cfg.get(f"network{index}Password", ""))
        if ssid:
            slots.append(WifiSlot(index=index, ssid=ssid, password=password))
    return slots


def _run_nmcli(args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["nmcli", *args],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )


def _scan_visible_ssids(interface: str) -> set[str]:
    result = _run_nmcli(["-t", "-f", "SSID", "dev", "wifi", "list", "ifname", interface, "--rescan", "yes"])
    if result.returncode != 0:
        logging.warning("Wi-Fi scan failed on %s: %s", interface, result.stderr.strip() or result.stdout.strip())
        return set()
    return {line.strip() for line in result.stdout.splitlines() if line.strip()}


def _current_ssid(interface: str) -> str | None:
    result = _run_nmcli(["-t", "-f", "ACTIVE,SSID", "dev", "wifi", "list", "ifname", interface])
    if result.returncode != 0:
        logging.debug("Unable to read current Wi-Fi SSID on %s: %s", interface, result.stderr.strip())
        return None
    for line in result.stdout.splitlines():
        if not line.startswith("yes:"):
            continue
        ssid = line.partition(":")[2].strip()
        if ssid:
            return ssid
    return None


def _connect_slot(interface: str, slot: WifiSlot) -> bool:
    connection_name = f"{CONNECTION_PREFIX}-{slot.index}"
    _run_nmcli(["connection", "delete", connection_name])

    args = [
        "--wait",
        "30",
        "dev",
        "wifi",
        "connect",
        slot.ssid,
        "ifname",
        interface,
        "name",
        connection_name,
    ]
    if slot.password:
        args.extend(["password", slot.password])

    result = _run_nmcli(args)
    if result.returncode == 0:
        logging.info("Connected Wi-Fi interface %s to preferred SSID %s", interface, slot.ssid)
        return True

    logging.warning(
        "Failed to connect Wi-Fi interface %s to SSID %s: %s",
        interface,
        slot.ssid,
        result.stderr.strip() or result.stdout.strip(),
    )
    return False
