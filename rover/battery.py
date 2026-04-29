from __future__ import annotations

import logging
from pathlib import Path

from rover.state import update_status_from_battery

try:
    from smbus2 import SMBus
except ImportError:  # pragma: no cover - fallback for Raspberry Pi OS packages
    try:
        from smbus import SMBus  # type: ignore
    except ImportError:  # pragma: no cover - handled at runtime with clear error
        SMBus = None  # type: ignore[assignment]


DEFAULT_POWER_SUPPLY_ROOT = Path("/sys/class/power_supply")

INA219_REG_CONFIG = 0x00
INA219_REG_SHUNT_VOLTAGE = 0x01
INA219_REG_BUS_VOLTAGE = 0x02
INA219_REG_POWER = 0x03
INA219_REG_CURRENT = 0x04
INA219_REG_CALIBRATION = 0x05

WAVESHARE_UPS_HAT_C_DEFAULT_I2C_BUS = 1
WAVESHARE_UPS_HAT_C_DEFAULT_I2C_ADDRESS = 0x43
WAVESHARE_UPS_HAT_C_DEFAULT_SHUNT_OHMS = 0.1
WAVESHARE_UPS_HAT_C_DEFAULT_MAX_CURRENT_A = 3.2
WAVESHARE_UPS_HAT_C_DEFAULT_MIN_VOLTAGE_V = 3.0
WAVESHARE_UPS_HAT_C_DEFAULT_MAX_VOLTAGE_V = 4.2


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


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(value, high))


def signed_16(value: int) -> int:
    return value - 65536 if value > 32767 else value


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


def read_register_word(bus: SMBus, address: int, register: int) -> int:
    value = bus.read_word_data(address, register)
    return ((value & 0xFF) << 8) | (value >> 8)


def write_register_word(bus: SMBus, address: int, register: int, value: int) -> None:
    swapped = ((value & 0xFF) << 8) | ((value >> 8) & 0xFF)
    bus.write_word_data(address, register, swapped)


def build_ina219_calibration(shunt_ohms: float, max_current_a: float) -> tuple[int, float]:
    current_lsb = max_current_a / 32767.0
    calibration = int(0.04096 / (current_lsb * shunt_ohms))
    return calibration, current_lsb


def voltage_to_percent(voltage_v: float, min_voltage_v: float, max_voltage_v: float) -> float:
    if max_voltage_v <= min_voltage_v:
        raise ValueError("battery maxVoltageV must be greater than minVoltageV")
    return clamp(((voltage_v - min_voltage_v) / (max_voltage_v - min_voltage_v)) * 100.0, 0.0, 100.0)


def read_waveshare_ups_hat_c_snapshot(battery_cfg: dict) -> dict:
    if SMBus is None:
        raise RuntimeError(
            "Missing I2C library. Install `smbus2` with pip or `python3-smbus` on Raspberry Pi OS."
        )

    i2c_bus = int(battery_cfg.get("i2cBus", WAVESHARE_UPS_HAT_C_DEFAULT_I2C_BUS))
    i2c_address = int(str(battery_cfg.get("i2cAddress", WAVESHARE_UPS_HAT_C_DEFAULT_I2C_ADDRESS)), 0)
    shunt_ohms = float(battery_cfg.get("shuntOhms", WAVESHARE_UPS_HAT_C_DEFAULT_SHUNT_OHMS))
    max_current_a = float(battery_cfg.get("maxCurrentA", WAVESHARE_UPS_HAT_C_DEFAULT_MAX_CURRENT_A))
    min_voltage_v = float(battery_cfg.get("minVoltageV", WAVESHARE_UPS_HAT_C_DEFAULT_MIN_VOLTAGE_V))
    max_voltage_v = float(battery_cfg.get("maxVoltageV", WAVESHARE_UPS_HAT_C_DEFAULT_MAX_VOLTAGE_V))

    calibration, current_lsb = build_ina219_calibration(shunt_ohms, max_current_a)
    power_lsb = current_lsb * 20.0

    with SMBus(i2c_bus) as bus:
        # 32V range, 320mV shunt range, 12-bit bus/shunt ADC, continuous shunt+bus conversion.
        write_register_word(bus, i2c_address, INA219_REG_CONFIG, 0x3EEF)
        write_register_word(bus, i2c_address, INA219_REG_CALIBRATION, calibration)

        bus_voltage_raw = read_register_word(bus, i2c_address, INA219_REG_BUS_VOLTAGE)
        current_raw = signed_16(read_register_word(bus, i2c_address, INA219_REG_CURRENT))
        power_raw = read_register_word(bus, i2c_address, INA219_REG_POWER)

    voltage_v = ((bus_voltage_raw >> 3) * 0.004)
    current_a = current_raw * current_lsb
    power_w = power_raw * power_lsb
    percent = voltage_to_percent(voltage_v, min_voltage_v, max_voltage_v)

    if current_a > 0.02:
        status = "CHARGING"
    elif current_a < -0.02:
        status = "DISCHARGING"
    else:
        status = "IDLE"

    return {
        "percent": percent,
        "voltage_v": voltage_v,
        "current_a": current_a,
        "power_w": power_w,
        "status": status,
        "present": True,
        "source": f"waveshare-ups-hat-c i2c-{i2c_bus}@{hex(i2c_address)}",
    }


def read_sysfs_battery_snapshot(battery_cfg: dict) -> dict:
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
        "current_a": None,
        "power_w": None,
        "status": status,
        "present": present,
        "source": str(battery_dir),
    }


def read_battery_snapshot(battery_cfg: dict) -> dict:
    driver = str(battery_cfg.get("driver", "waveshare-ups-hat-c")).strip().lower()
    if driver == "waveshare-ups-hat-c":
        return read_waveshare_ups_hat_c_snapshot(battery_cfg)
    if driver == "sysfs":
        return read_sysfs_battery_snapshot(battery_cfg)
    raise ValueError(f"Unsupported battery driver: {driver}")


def battery_monitor_loop(battery_cfg: dict, stop_event) -> None:
    poll_interval = float(battery_cfg.get("pollIntervalSec", 10))
    last_logged_source = None

    while not stop_event.is_set():
        try:
            snapshot = read_battery_snapshot(battery_cfg)
            update_status_from_battery(
                percent=snapshot["percent"],
                voltage_v=snapshot["voltage_v"],
                current_a=snapshot["current_a"],
                power_w=snapshot["power_w"],
                status=snapshot["status"],
                present=snapshot["present"],
                error=None,
            )

            source = snapshot["source"]
            if source != last_logged_source:
                logging.info("Battery monitor using %s", source)
                last_logged_source = source
        except Exception as exc:
            logging.error("Battery monitor error: %s", exc)
            update_status_from_battery(
                percent=None,
                voltage_v=None,
                current_a=None,
                power_w=None,
                status="ERROR",
                present=None,
                error=str(exc),
            )

        stop_event.wait(poll_interval)
