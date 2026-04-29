from __future__ import annotations

import threading
import time
from collections import Counter, deque
from dataclasses import dataclass
from typing import Optional


FIX_MODE_ENUM = {
    "NO FIX": 0,
    "GNSS FIX": 1,
    "DGPS": 2,
    "RTK FLOAT": 3,
    "RTK FIXED": 4,
}

NTRIP_STATUS_ENUM = {
    False: 0,
    True: 1,
}

RTCM_PREAMBLE = 0xD3
RTCM_CRC24Q_POLY = 0x1864CFB
RTCM_OBSERVATION_TYPES = {
    1074, 1075, 1077,
    1084, 1085, 1087,
    1094, 1095, 1097,
    1114, 1115, 1117,
    1124, 1125, 1127,
}
RTCM_MESSAGE_NAMES = {
    1005: "ARP",
    1006: "ARP+H",
    1019: "GPS EPH",
    1020: "GLO EPH",
    1033: "ANT/RCV DESC",
    1042: "BDS EPH",
    1044: "QZSS EPH",
    1046: "GAL EPH",
    1074: "GPS MSM4",
    1075: "GPS MSM5",
    1077: "GPS MSM7",
    1084: "GLO MSM4",
    1085: "GLO MSM5",
    1087: "GLO MSM7",
    1094: "GAL MSM4",
    1095: "GAL MSM5",
    1097: "GAL MSM7",
    1114: "QZS MSM4",
    1115: "QZS MSM5",
    1117: "QZS MSM7",
    1124: "BDS MSM4",
    1125: "BDS MSM5",
    1127: "BDS MSM7",
    1230: "GLO BIAS",
}


@dataclass
class RoverStatus:
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    altitude_m: Optional[float] = None
    satellites: Optional[int] = None
    hdop: Optional[float] = None
    fix_quality: Optional[int] = None
    fix_label: str = "UNKNOWN"
    ntrip_connected: bool = False
    ntrip_last_error: Optional[str] = None
    last_rtcm_received_at: Optional[float] = None
    last_nmea_at: Optional[float] = None
    rtcm_bytes: int = 0
    rtcm_frames: int = 0
    rtcm_last_type: Optional[int] = None
    rtcm_has_station_frame: bool = False
    rtcm_has_observation_frame: bool = False
    rtcm_recent_types: str = "-"
    battery_percent: Optional[float] = None
    battery_voltage_v: Optional[float] = None
    battery_status: str = "UNKNOWN"
    battery_present: Optional[bool] = None
    battery_last_update_at: Optional[float] = None
    battery_last_error: Optional[str] = None


STATUS_LOCK = threading.Lock()
STATUS = RoverStatus()

LATEST_GGA_LOCK = threading.Lock()
LATEST_GGA: Optional[str] = None


class RtcmStreamInspector:
    def __init__(self) -> None:
        self._buffer = bytearray()
        self._recent = deque(maxlen=8)
        self._type_counter: Counter[int] = Counter()

    def feed(self, data: bytes) -> list[int]:
        self._buffer.extend(data)
        parsed_types: list[int] = []

        while True:
            if len(self._buffer) < 6:
                break

            preamble_idx = self._buffer.find(bytes([RTCM_PREAMBLE]))
            if preamble_idx < 0:
                self._buffer.clear()
                break

            if preamble_idx > 0:
                del self._buffer[:preamble_idx]

            if len(self._buffer) < 6:
                break

            payload_length = ((self._buffer[1] & 0x03) << 8) | self._buffer[2]
            frame_length = 3 + payload_length + 3
            if len(self._buffer) < frame_length:
                break

            frame = bytes(self._buffer[:frame_length])
            del self._buffer[:frame_length]

            expected_crc = int.from_bytes(frame[-3:], byteorder="big")
            actual_crc = self.crc24q(frame[:-3])
            if actual_crc != expected_crc:
                continue

            message_type = ((frame[3] << 4) | (frame[4] >> 4)) & 0x0FFF
            parsed_types.append(message_type)
            self._recent.append(message_type)
            self._type_counter[message_type] += 1

        return parsed_types

    @staticmethod
    def crc24q(data: bytes) -> int:
        crc = 0
        for byte in data:
            crc ^= byte << 16
            for _ in range(8):
                crc <<= 1
                if crc & 0x1000000:
                    crc ^= RTCM_CRC24Q_POLY
        return crc & 0xFFFFFF

    def describe_recent(self) -> str:
        if not self._recent:
            return "-"
        parts = []
        for msg_type in self._recent:
            label = RTCM_MESSAGE_NAMES.get(msg_type, "")
            parts.append(f"{msg_type}:{label}" if label else str(msg_type))
        return ", ".join(parts)


def fix_quality_to_label(quality: Optional[int]) -> str:
    mapping = {
        0: "NO FIX",
        1: "GNSS FIX",
        2: "DGPS",
        4: "RTK FIXED",
        5: "RTK FLOAT",
        6: "DEAD RECKONING",
    }
    if quality is None:
        return "UNKNOWN"
    return mapping.get(quality, f"QUALITY {quality}")


def fmt(value: Optional[float], digits: int = 6) -> str:
    if value is None:
        return "-"
    return f"{value:.{digits}f}"


def fmt_int(value: Optional[int]) -> str:
    if value is None:
        return "-"
    return str(value)


def fmt_percent(value: Optional[float], digits: int = 1) -> str:
    if value is None:
        return "-"
    return f"{value:.{digits}f}%"


def store_latest_gga(raw_data: bytes) -> None:
    global LATEST_GGA
    try:
        line = raw_data.decode("ascii", errors="ignore").strip()
    except Exception:
        return

    if not line.startswith("$"):
        return

    with LATEST_GGA_LOCK:
        LATEST_GGA = line


def get_latest_gga() -> Optional[str]:
    with LATEST_GGA_LOCK:
        return LATEST_GGA


def update_status_from_gga(parsed) -> None:
    with STATUS_LOCK:
        STATUS.latitude = getattr(parsed, "lat", None)
        STATUS.longitude = getattr(parsed, "lon", None)
        STATUS.altitude_m = getattr(parsed, "alt", None)
        STATUS.satellites = getattr(parsed, "numSV", None)
        STATUS.hdop = getattr(parsed, "HDOP", None)
        STATUS.fix_quality = getattr(parsed, "quality", None)
        STATUS.fix_label = fix_quality_to_label(STATUS.fix_quality)
        STATUS.last_nmea_at = time.time()


def update_status_from_gsa(parsed) -> None:
    with STATUS_LOCK:
        hdop = getattr(parsed, "HDOP", None)
        if hdop is not None:
            STATUS.hdop = hdop
        STATUS.last_nmea_at = time.time()


def update_status_from_rtcm(
    rtcm_types: list[int],
    byte_count: int,
    inspector: RtcmStreamInspector,
) -> None:
    with STATUS_LOCK:
        STATUS.rtcm_bytes += byte_count
        STATUS.rtcm_frames += len(rtcm_types)
        if rtcm_types:
            STATUS.rtcm_last_type = rtcm_types[-1]
            STATUS.rtcm_has_station_frame = STATUS.rtcm_has_station_frame or any(
                msg_type in {1005, 1006} for msg_type in rtcm_types
            )
            STATUS.rtcm_has_observation_frame = STATUS.rtcm_has_observation_frame or any(
                msg_type in RTCM_OBSERVATION_TYPES for msg_type in rtcm_types
            )
            STATUS.rtcm_recent_types = inspector.describe_recent()


def update_status_from_battery(
    *,
    percent: Optional[float],
    voltage_v: Optional[float],
    status: Optional[str],
    present: Optional[bool],
    error: Optional[str] = None,
) -> None:
    with STATUS_LOCK:
        STATUS.battery_percent = percent
        STATUS.battery_voltage_v = voltage_v
        STATUS.battery_status = status or "UNKNOWN"
        STATUS.battery_present = present
        STATUS.battery_last_update_at = time.time()
        STATUS.battery_last_error = error
