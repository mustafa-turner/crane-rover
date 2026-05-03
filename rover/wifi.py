from __future__ import annotations

import logging
import shutil
import subprocess
import time
from dataclasses import dataclass


CONNECTION_PREFIX = "crane-rover-wifi"
DEFAULT_INTERFACE = "wlan0"
SLOT_COUNT = 4
WIFI_POLL_INTERVAL_SEC = 15.0


@dataclass
class WifiSlot:
    index: int
    ssid: str
    password: str


def apply_preferred_wifi(wifi_cfg: dict) -> None:
    _ensure_wifi(wifi_cfg, reconnect_only=False)


def wifi_monitor_loop(wifi_cfg: dict, stop_event) -> None:
    if not wifi_cfg.get("enabled", False):
        return

    poll_interval = WIFI_POLL_INTERVAL_SEC
    logging.info("Wi-Fi monitor started interface=%s poll_interval=%ss", _interface_name(wifi_cfg), poll_interval)
    while not stop_event.is_set():
        try:
            _ensure_wifi(wifi_cfg, reconnect_only=True)
        except Exception as exc:
            logging.warning("Wi-Fi monitor error: %s", exc)
        stop_event.wait(poll_interval)
    logging.info("Wi-Fi monitor stopped")


def _ensure_wifi(wifi_cfg: dict, *, reconnect_only: bool) -> None:
    if not wifi_cfg.get("enabled", False):
        return

    if shutil.which("nmcli") is None:
        logging.warning("Wi-Fi auto-connect is enabled but nmcli is not installed")
        return

    interface = _interface_name(wifi_cfg)
    slots = _configured_slots(wifi_cfg)
    if not slots:
        logging.info("Wi-Fi auto-connect enabled but no SSIDs are configured")
        return

    current_ssid = _current_ssid(interface)
    if reconnect_only and current_ssid:
        return

    visible_ssids = _scan_visible_ssids(interface)
    if not visible_ssids:
        logging.warning("Wi-Fi scan returned no visible SSIDs on %s", interface)
        return

    for slot in slots:
        if slot.ssid not in visible_ssids:
            continue
        if current_ssid == slot.ssid:
            logging.info("Wi-Fi already connected to preferred SSID %s", slot.ssid)
            return
        if _connect_slot(interface, slot):
            return

    logging.warning("None of the configured Wi-Fi SSIDs are currently available on %s", interface)


def _interface_name(wifi_cfg: dict) -> str:
    return str(wifi_cfg.get("interface", DEFAULT_INTERFACE)).strip() or DEFAULT_INTERFACE


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
