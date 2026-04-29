from __future__ import annotations

import logging
from pathlib import Path

from rover.state import update_status_from_battery


DEFAULT_POWER_SUPPLY_ROOT = Path("/sys/class/power_supply")


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8").strip()


def parse_optional_float(text: str) -> float | None:
    text = text.strip()
    if not text:
        return None
    return float(text)


def parse_present(text: str) -> bool | None:
    normalized = text.strip().lower()
    if normalized in {"1", "true", "yes", "present"}:
        return True
    if normalized in {"0", "false", "no", "absent"}:
        return False
    return None


def normalize_voltage(raw_value: float | None) -> float | None:
    if raw_value is None:
        return None
    if raw_value > 1000:
        return raw_value / 1_000_000.0
    return raw_value


def discover_battery_dir(root: Path = DEFAULT_POWER_SUPPLY_ROOT) -> Path | None:
    if not root.exists():
        return None

    preferred_names = ("battery", "bat0", "bat1", "axp20x-battery", "ups", "ups-battery")
    for name in preferred_names:
        candidate = root / name
        if (candidate / "capacity").exists():
            return candidate

    for candidate in sorted(root.iterdir()):
        if candidate.is_dir() and (candidate / "capacity").exists():
            return candidate

    return None


def resolve_battery_dir(battery_cfg: dict) -> Path | None:
    base_path = battery_cfg.get("basePath")
    if base_path:
        return Path(base_path)
    return discover_battery_dir()


def read_battery_snapshot(battery_cfg: dict) -> dict:
    battery_dir = resolve_battery_dir(battery_cfg)
    if battery_dir is None:
        raise FileNotFoundError("No battery power-supply directory found under /sys/class/power_supply")

    capacity_path = Path(battery_cfg.get("capacityPath", battery_dir / "capacity"))
    voltage_path = Path(battery_cfg.get("voltageNowPath", battery_dir / "voltage_now"))
    status_path = Path(battery_cfg.get("statusPath", battery_dir / "status"))
    present_path = Path(battery_cfg.get("presentPath", battery_dir / "present"))

    percent = None
    voltage_v = None
    status = None
    present = None

    if capacity_path.exists():
        percent = parse_optional_float(read_text(capacity_path))

    if voltage_path.exists():
        voltage_v = normalize_voltage(parse_optional_float(read_text(voltage_path)))

    if status_path.exists():
        status = read_text(status_path).upper()

    if present_path.exists():
        present = parse_present(read_text(present_path))

    return {
        "percent": percent,
        "voltage_v": voltage_v,
        "status": status,
        "present": present,
        "battery_dir": str(battery_dir),
    }


def battery_monitor_loop(battery_cfg: dict, stop_event) -> None:
    poll_interval = float(battery_cfg.get("pollIntervalSec", 10))
    last_logged_source = None

    while not stop_event.is_set():
        try:
            snapshot = read_battery_snapshot(battery_cfg)
            update_status_from_battery(
                percent=snapshot["percent"],
                voltage_v=snapshot["voltage_v"],
                status=snapshot["status"],
                present=snapshot["present"],
                error=None,
            )

            battery_dir = snapshot["battery_dir"]
            if battery_dir != last_logged_source:
                logging.info("Battery monitor using %s", battery_dir)
                last_logged_source = battery_dir
        except Exception as exc:
            logging.error("Battery monitor error: %s", exc)
            update_status_from_battery(
                percent=None,
                voltage_v=None,
                status="ERROR",
                present=None,
                error=str(exc),
            )

        stop_event.wait(poll_interval)
