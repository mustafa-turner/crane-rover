from __future__ import annotations

import logging
import os
from copy import deepcopy

import yaml


PUBLIC_CONFIG_TEMPLATE = {
    "serial": {
        "port": "/dev/serial0",
        "baudrate": 115200,
    },
    "wifi": {
        "enabled": False,
        "interface": "wlan0",
        "network1Ssid": "",
        "network1Password": "",
        "network2Ssid": "",
        "network2Password": "",
        "network3Ssid": "",
        "network3Password": "",
        "network4Ssid": "",
        "network4Password": "",
    },
    "ntrip": {
        "host": "",
        "port": 2101,
        "mountpoint": "",
        "username": "",
        "password": "",
    },
    "logging": {
        "level": "INFO",
    },
    "status": {
        "enabled": True,
        "mode": "normal",
    },
    "battery": {
        "enabled": True,
        "driver": "waveshare-ups-hat-c",
        "i2cBus": 1,
        "i2cAddress": "0x43",
        "minVoltageV": 3.0,
        "maxVoltageV": 4.2,
    },
    "peerUdp": {
        "enabled": True,
        "deviceId": "rover-01",
        "port": 5005,
        "broadcastHost": "255.255.255.255",
        "extraTargets": [],
    },
    "blynk": {
        "enabled": True,
        "broker": "blynk.cloud",
        "port": 8883,
        "username": "device",
        "authToken": "",
        "templateId": "",
        "firmwareVersion": "0.1.0",
    },
}


def load_config(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        loaded = yaml.safe_load(f) or {}
    return normalize_config(loaded)


def save_config(path: str, config: dict) -> None:
    config = normalize_config(config)
    tmp_path = f"{path}.tmp"
    with open(tmp_path, "w", encoding="utf-8") as f:
        yaml.safe_dump(config, f, sort_keys=False, default_flow_style=False)
    os.replace(tmp_path, path)


def setup_logging(level: str, *, handlers: list[logging.Handler] | None = None) -> None:
    numeric_level = getattr(logging, level.upper(), logging.INFO)
    logging.basicConfig(
        level=numeric_level,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=handlers,
    )


def normalize_config(config: dict) -> dict:
    return _merge_public_config(PUBLIC_CONFIG_TEMPLATE, config or {})


def _merge_public_config(template: dict, provided: dict) -> dict:
    merged = deepcopy(template)
    for key, default_value in template.items():
        provided_value = provided.get(key)
        if isinstance(default_value, dict):
            merged[key] = _merge_public_config(default_value, provided_value if isinstance(provided_value, dict) else {})
        elif isinstance(default_value, list):
            merged[key] = list(provided_value) if isinstance(provided_value, list) else list(default_value)
        else:
            merged[key] = default_value if provided_value is None else provided_value
    return merged
