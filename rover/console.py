from __future__ import annotations

import logging
import os
import select
import shlex
import sys
import termios
import threading
import time
import tty
from dataclasses import dataclass
from typing import Any

import serial

from rover.config import save_config


SECTION_ORDER = [
    "serial",
    "wifi",
    "ntrip",
    "logging",
    "status",
    "battery",
    "peerUdp",
    "blynk",
    "mqtt",
]

CONSOLE_ENABLED = True
CONSOLE_PORT = "/dev/ttyGS0"
CONSOLE_BAUDRATE = 115200
CONSOLE_READ_TIMEOUT_SEC = 0.2
CONSOLE_WRITE_TIMEOUT_SEC = 0.2

SECTION_HELP = {
    "serial": "GNSS receiver serial port settings.",
    "wifi": "Preferred Wi-Fi networks, checked in priority order for startup and failover.",
    "ntrip": "NTRIP caster connection settings for RTCM corrections.",
    "logging": "Application logging verbosity.",
    "status": "Operator status screen controls.",
    "battery": "Battery monitoring configuration.",
    "peerUdp": "Peer rover UDP sharing and optional ZeroTier unicast targets.",
    "blynk": "Blynk MQTT publishing settings.",
    "mqtt": "Second MQTT publisher settings for a non-Blynk broker.",
}

FIELD_HELP = {
    "serial.port": "Serial device path used for the GNSS receiver, for example /dev/serial0.",
    "serial.baudrate": "Baud rate for the GNSS receiver serial link, for example 115200.",
    "wifi.enabled": "Enable Wi-Fi startup selection and automatic failover between configured SSIDs.",
    "wifi.interface": "Linux Wi-Fi interface name, usually wlan0.",
    "wifi.network1Ssid": "Highest-priority Wi-Fi SSID.",
    "wifi.network1Password": "Password for network1Ssid.",
    "wifi.network2Ssid": "Second-priority Wi-Fi SSID.",
    "wifi.network2Password": "Password for network2Ssid.",
    "wifi.network3Ssid": "Third-priority Wi-Fi SSID.",
    "wifi.network3Password": "Password for network3Ssid.",
    "wifi.network4Ssid": "Fourth-priority Wi-Fi SSID.",
    "wifi.network4Password": "Password for network4Ssid.",
    "ntrip.host": "NTRIP caster hostname or IP address.",
    "ntrip.port": "NTRIP caster TCP port, usually 2101.",
    "ntrip.mountpoint": "NTRIP mountpoint name.",
    "ntrip.username": "NTRIP username.",
    "ntrip.password": "NTRIP password.",
    "logging.level": "Logging verbosity.",
    "status.enabled": "Enable the live rover status screen.",
    "status.mode": "Status screen detail level.",
    "status.intervalSec": "Seconds between status screen refreshes. Very low values can flood the console.",
    "battery.enabled": "Enable battery monitoring.",
    "battery.driver": "Battery driver to use.",
    "battery.i2cBus": "I2C bus number for the battery monitor, usually 1.",
    "battery.i2cAddress": "I2C address for the battery monitor, usually 0x43.",
    "battery.minVoltageV": "Battery voltage treated as empty for percentage estimation.",
    "battery.maxVoltageV": "Battery voltage treated as full for percentage estimation.",
    "peerUdp.enabled": "Enable peer rover UDP send and receive.",
    "peerUdp.deviceId": "Unique rover identifier shared with peers.",
    "peerUdp.port": "UDP port used by all rovers.",
    "peerUdp.broadcastHost": "IPv4 broadcast address for local-LAN peer discovery, usually 255.255.255.255.",
    "peerUdp.bufferDistanceM": "Straight-line antenna-to-edge buffer distance in meters, subtracted from peer separation.",
    "peerUdp.extraTargets": "Optional comma-separated list of unicast peer IPs, usually ZeroTier addresses.",
    "blynk.enabled": "Enable Blynk MQTT publishing.",
    "blynk.broker": "Blynk broker hostname.",
    "blynk.port": "Blynk MQTT port, usually 8883.",
    "blynk.username": "Blynk MQTT username, usually device.",
    "blynk.authToken": "Blynk device auth token.",
    "blynk.templateId": "Blynk template ID.",
    "blynk.firmwareVersion": "Firmware version string reported to Blynk.",
    "blynk.publishIntervalSec": "Seconds between Blynk publishes. Supports decimals.",
    "mqtt.enabled": "Enable the second MQTT publisher.",
    "mqtt.broker": "Second MQTT broker hostname or IP address.",
    "mqtt.port": "Second MQTT broker port. Port 8883 enables TLS automatically.",
    "mqtt.username": "Optional MQTT username for the second broker.",
    "mqtt.password": "Optional MQTT password for the second broker.",
    "mqtt.topic": "Topic used for the second MQTT publish stream.",
    "mqtt.publishIntervalSec": "Seconds between second-broker publishes. Supports decimals.",
}

FIELD_OPTIONS = {
    "wifi.enabled": "Options: true, false",
    "logging.level": "Options: DEBUG, INFO, WARNING, ERROR, CRITICAL",
    "status.enabled": "Options: true, false",
    "status.mode": "Options: normal, debug",
    "battery.enabled": "Options: true, false",
    "battery.driver": "Options: waveshare-ups-hat-c, sysfs",
    "peerUdp.enabled": "Options: true, false",
    "blynk.enabled": "Options: true, false",
    "mqtt.enabled": "Options: true, false",
}


def format_value(value: Any, *, key: str = "") -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, list):
        return ", ".join(str(item) for item in value) if value else "[]"
    if isinstance(value, int) and "address" in key.lower():
        return f"0x{value:x} ({value})"
    return str(value)


def parse_scalar(raw: str, current: Any) -> Any:
    text = raw.strip()
    if text == "":
        return current

    lowered = text.lower()
    if lowered in {"null", "none"}:
        return None

    if isinstance(current, bool):
        if lowered in {"true", "t", "1", "yes", "y", "on"}:
            return True
        if lowered in {"false", "f", "0", "no", "n", "off"}:
            return False
        raise ValueError("enter true/false, yes/no, on/off, or 1/0")

    if isinstance(current, int) and not isinstance(current, bool):
        return int(text, 0)

    if isinstance(current, float):
        return float(text)

    return text


def parse_list(raw: str, current: list[Any]) -> list[str]:
    text = raw.strip()
    if text == "":
        return current
    if text == "[]":
        return []
    return [item.strip() for item in text.split(",") if item.strip()]


def ordered_items(node: dict[str, Any]) -> list[tuple[str, Any]]:
    seen: set[str] = set()
    items: list[tuple[str, Any]] = []
    for key in SECTION_ORDER:
        if key in node:
            items.append((key, node[key]))
            seen.add(key)
    for key, value in node.items():
        if key not in seen:
            items.append((key, value))
    return items


def get_section_help(path: str) -> str | None:
    return SECTION_HELP.get(path)


def get_field_help(path: str) -> str | None:
    return FIELD_HELP.get(path)


def get_field_options(path: str) -> str | None:
    return FIELD_OPTIONS.get(path)


@dataclass
class MenuResult:
    restart_requested: bool = False


class _OutputTarget:
    def write(self, text: str) -> None:
        raise NotImplementedError

    def read_line(self, prompt: str) -> str:
        raise NotImplementedError


class _TtyTarget(_OutputTarget):
    def __init__(self, manager: "SerialConsoleManager") -> None:
        self._manager = manager
        self.enabled = sys.stdin.isatty() and sys.stdout.isatty()
        self._fd: int | None = None
        self._old_termios: list[Any] | None = None

    def start(self) -> None:
        if not self.enabled:
            return
        self._fd = sys.stdin.fileno()
        self._old_termios = termios.tcgetattr(self._fd)
        tty.setcbreak(self._fd)

    def stop(self) -> None:
        self.restore_canonical()

    def poll_menu_request(self) -> bool:
        if not self.enabled or self._fd is None:
            return False
        ready, _, _ = select.select([self._fd], [], [], 0.2)
        if not ready:
            return False
        try:
            os.read(self._fd, 1)
        except OSError:
            return False
        return True

    def restore_canonical(self) -> None:
        if self._fd is None or self._old_termios is None:
            return
        termios.tcsetattr(self._fd, termios.TCSADRAIN, self._old_termios)

    def set_cbreak(self) -> None:
        if self.enabled and self._fd is not None:
            tty.setcbreak(self._fd)

    def write(self, text: str) -> None:
        sys.stdout.write(text)
        sys.stdout.flush()

    def read_line(self, prompt: str) -> str:
        return input(prompt)


class _SerialPortTarget(_OutputTarget):
    def __init__(self, manager: "SerialConsoleManager", gnss_port: str | None) -> None:
        self._manager = manager
        self._gnss_port = gnss_port
        self.enabled = CONSOLE_ENABLED
        self._serial: serial.Serial | None = None
        self._write_lock = threading.Lock()

    @property
    def port(self) -> str:
        return CONSOLE_PORT

    def start(self) -> None:
        if not self.enabled:
            return

        if not self.port:
            logging.warning("Console is enabled but CONSOLE_PORT is empty")
            self.enabled = False
            return

        if self._gnss_port and self.port == self._gnss_port:
            logging.error("Console port %s matches GNSS serial port; disabling console", self.port)
            self.enabled = False
            return

        baudrate = CONSOLE_BAUDRATE
        timeout = CONSOLE_READ_TIMEOUT_SEC
        write_timeout = CONSOLE_WRITE_TIMEOUT_SEC
        try:
            self._serial = serial.Serial(
                port=self.port,
                baudrate=baudrate,
                timeout=timeout,
                write_timeout=write_timeout,
            )
        except serial.SerialException as exc:
            logging.error("Failed to open console serial port %s: %s", self.port, exc)
            self.enabled = False
            return
        self.write("\r\n[crane-rover] serial console ready\r\n")

    def stop(self) -> None:
        if self._serial is None:
            return
        try:
            self._serial.close()
        finally:
            self._serial = None

    def poll_menu_request(self) -> bool:
        if not self.enabled or self._serial is None:
            time.sleep(0.2)
            return False
        try:
            data = self._serial.read(1)
        except serial.SerialException as exc:
            logging.error("Console serial read error: %s", exc)
            time.sleep(1)
            return False
        return bool(data)

    def write(self, text: str) -> None:
        if self._serial is None:
            return
        payload = text.replace("\n", "\r\n").encode("utf-8", errors="replace")
        with self._write_lock:
            try:
                self._serial.write(payload)
            except serial.SerialException:
                pass

    def read_line(self, prompt: str) -> str:
        if self._serial is None:
            return ""

        self.write(prompt)
        chars: list[str] = []
        while not self._manager.stop_requested():
            try:
                data = self._serial.read(1)
            except serial.SerialException as exc:
                logging.error("Console serial read error: %s", exc)
                return ""

            if not data:
                continue

            byte = data[0]
            if byte in (10, 13):
                self.write("\n")
                return "".join(chars)
            if byte in (8, 127):
                if chars:
                    chars.pop()
                    self.write("\b \b")
                continue

            try:
                char = data.decode("utf-8", errors="ignore")
            except Exception:
                char = ""
            if not char:
                continue
            chars.append(char)
            self.write(char)

        return ""


class _SerialConsoleLogHandler(logging.Handler):
    def __init__(self, manager: "SerialConsoleManager", target: _SerialPortTarget) -> None:
        super().__init__()
        self._manager = manager
        self._target = target

    def emit(self, record: logging.LogRecord) -> None:
        if self._manager.menu_active.is_set():
            return
        try:
            message = self.format(record)
            self._target.write(message + "\n")
        except Exception:
            self.handleError(record)


class SerialConsoleManager:
    def __init__(self, config_path: str, config: dict[str, Any]) -> None:
        self.config_path = config_path
        self.menu_active = threading.Event()
        self._menu_requested = threading.Event()
        self._stop_event = threading.Event()
        self._active_target: _OutputTarget | None = None
        self._monitor_thread: threading.Thread | None = None
        self._tty = _TtyTarget(self)
        gnss_port = config.get("serial", {}).get("port")
        self._serial = _SerialPortTarget(self, gnss_port)

    def create_log_handlers(self) -> list[logging.Handler]:
        handlers: list[logging.Handler] = [logging.StreamHandler(sys.stdout)]
        if self._serial.enabled:
            serial_handler = _SerialConsoleLogHandler(self, self._serial)
            serial_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
            handlers.append(serial_handler)
        return handlers

    def start(self) -> None:
        self._tty.start()
        self._serial.start()
        self._monitor_thread = threading.Thread(target=self._monitor_loop, daemon=True)
        self._monitor_thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._monitor_thread is not None:
            self._monitor_thread.join(timeout=1)
        self._tty.stop()
        self._serial.stop()

    def stop_requested(self) -> bool:
        return self._stop_event.is_set()

    def should_pause_live_output(self) -> bool:
        return self.menu_active.is_set()

    def consume_menu_request(self) -> bool:
        if not self._menu_requested.is_set():
            return False
        self._menu_requested.clear()
        return True

    def run_menu(self, config: dict[str, Any]) -> MenuResult:
        result = MenuResult()
        target = self._active_target
        if target is None:
            return result

        self.menu_active.set()
        self._tty.restore_canonical()
        try:
            while True:
                self._write(target, "\n=== SETTINGS ===\n")
                sections = ordered_items(config)
                for idx, (key, value) in enumerate(sections, start=1):
                    label = "section" if isinstance(value, dict) else format_value(value, key=key)
                    self._write(target, f"{idx}) {key} [{label}]\n")
                self._write(target, "s) save and restart\n")
                self._write(target, "q) exit without saving\n")
                choice = target.read_line("Select: ").strip().lower()
                if choice == "q":
                    break
                if choice == "s":
                    try:
                        save_config(self.config_path, config)
                    except OSError as exc:
                        self._write(target, f"Save failed: {exc}\n")
                        hint = self._save_error_hint(exc)
                        if hint:
                            self._write(target, hint)
                        self._write(target, "Fix the issue, then choose s again. Choose q to exit without saving.\n")
                        continue
                    self._write(target, f"Saved {self.config_path}. Restarting...\n")
                    result.restart_requested = True
                    break
                if not choice.isdigit():
                    self._write(target, "Invalid selection.\n")
                    continue

                index = int(choice) - 1
                if index < 0 or index >= len(sections):
                    self._write(target, "Invalid selection.\n")
                    continue

                section_key, section_value = sections[index]
                if isinstance(section_value, dict):
                    self._edit_mapping(target, section_key, section_value)
                else:
                    config[section_key] = self._prompt_for_scalar(target, section_key, section_value)
        finally:
            self.menu_active.clear()
            self._active_target = None
            if not self._stop_event.is_set():
                self._tty.set_cbreak()
        return result

    def write_status_block(self, lines: list[str]) -> None:
        payload = "\n".join(lines) + "\n"
        sys.stdout.write(payload)
        sys.stdout.flush()
        if self._serial.enabled and not self.menu_active.is_set():
            self._serial.write(payload)

    def _edit_mapping(self, target: _OutputTarget, title: str, node: dict[str, Any]) -> None:
        while True:
            self._write(target, f"\n=== {title} ===\n")
            section_help = get_section_help(title)
            if section_help:
                self._write(target, f"{section_help}\n")
            items = ordered_items(node)
            for idx, (key, value) in enumerate(items, start=1):
                label = "section" if isinstance(value, dict) else format_value(value, key=key)
                self._write(target, f"{idx}) {key} = {label}\n")
            self._write(target, "b) back\n")
            choice = target.read_line("Select: ").strip().lower()
            if choice == "b":
                return
            if not choice.isdigit():
                self._write(target, "Invalid selection.\n")
                continue

            index = int(choice) - 1
            if index < 0 or index >= len(items):
                self._write(target, "Invalid selection.\n")
                continue

            key, value = items[index]
            field_path = f"{title}.{key}"
            if isinstance(value, dict):
                self._edit_mapping(target, field_path, value)
            elif isinstance(value, list):
                node[key] = self._prompt_for_list(target, field_path, value)
            else:
                node[key] = self._prompt_for_scalar(target, field_path, value)

    def _prompt_for_scalar(self, target: _OutputTarget, path: str, current: Any) -> Any:
        help_text = get_field_help(path)
        options_text = get_field_options(path)
        if help_text:
            self._write(target, f"{help_text}\n")
        if options_text:
            self._write(target, f"{options_text}\n")
        self._write(target, f"Current value for {path}: {format_value(current, key=path)}\n")
        self._write(target, "Press Enter to keep the current value.\n")
        while True:
            raw = target.read_line("New value: ")
            try:
                new_value = parse_scalar(raw, current)
                self._write(target, f"Updated {path} to {format_value(new_value, key=path)}\n")
                return new_value
            except ValueError as exc:
                self._write(target, f"Invalid value: {exc}\n")

    def _prompt_for_list(self, target: _OutputTarget, path: str, current: list[Any]) -> list[str]:
        help_text = get_field_help(path)
        if help_text:
            self._write(target, f"{help_text}\n")
        self._write(target, f"Current value for {path}: {format_value(current, key=path)}\n")
        self._write(target, "Enter a comma-separated list. Use [] to clear it. Press Enter to keep the current value.\n")
        while True:
            raw = target.read_line("New value: ")
            try:
                new_value = parse_list(raw, current)
                self._write(target, f"Updated {path} to {format_value(new_value, key=path)}\n")
                return new_value
            except ValueError as exc:
                self._write(target, f"Invalid value: {exc}\n")

    def _monitor_loop(self) -> None:
        while not self._stop_event.is_set():
            if self.menu_active.is_set() or self._menu_requested.is_set():
                time.sleep(0.1)
                continue

            if self._tty.poll_menu_request():
                self._active_target = self._tty
                self._menu_requested.set()
                continue

            if self._serial.poll_menu_request():
                self._active_target = self._serial
                self._menu_requested.set()

    @staticmethod
    def _write(target: _OutputTarget, text: str) -> None:
        target.write(text)

    def _save_error_hint(self, exc: OSError) -> str:
        if not isinstance(exc, PermissionError):
            return ""

        path = os.path.abspath(self.config_path)
        directory = os.path.dirname(path) or "."
        user, group = _current_owner_labels()
        quoted_directory = shlex.quote(directory)
        quoted_path = shlex.quote(path)
        return (
            f"The rover process is running as {user} and needs write access to both:\n"
            f"  {directory}\n"
            f"  {path}\n"
            "On the Pi, fix ownership with:\n"
            f"  sudo chown {user}:{group} {quoted_directory} {quoted_path}\n"
        )


def _current_owner_labels() -> tuple[str, str]:
    try:
        import grp
        import pwd

        user = pwd.getpwuid(os.geteuid()).pw_name
        group = grp.getgrgid(os.getegid()).gr_name
        return user, group
    except Exception:
        return str(os.geteuid()), str(os.getegid())
