from __future__ import annotations

import json
import logging
import math
import socket
import time
from pathlib import Path

from rover.state import (
    PeerStatus,
    STATUS,
    STATUS_LOCK,
    update_local_horizontal_accuracy,
    update_peer_runtime_state,
    upsert_peer_status,
)


DEFAULT_FIX_ACCURACY_M = {
    "UNKNOWN": None,
    "NO FIX": None,
    "GNSS FIX": 3.0,
    "DGPS": 1.0,
    "RTK FLOAT": 0.2,
    "RTK FIXED": 0.02,
    "DEAD RECKONING": 10.0,
}

SAFETY_TOLERANCE_LOOKUP_PATH = Path(__file__).with_name("safety_tolerance_lookup.json")

PEER_SCHEMA = "crane-rover-peer-v1"
EARTH_RADIUS_M = 6_371_000.0


def load_safety_tolerance_lookup() -> dict[tuple[str, str], float]:
    try:
        with SAFETY_TOLERANCE_LOOKUP_PATH.open("r", encoding="utf-8") as f:
            raw_lookup = json.load(f)
    except FileNotFoundError:
        logging.warning("Safety tolerance lookup file not found: %s", SAFETY_TOLERANCE_LOOKUP_PATH)
        return {}
    except (OSError, json.JSONDecodeError) as exc:
        logging.warning("Safety tolerance lookup file invalid: %s", exc)
        return {}

    lookup: dict[tuple[str, str], float] = {}
    if not isinstance(raw_lookup, dict):
        logging.warning("Safety tolerance lookup must be a JSON object")
        return lookup

    for local_fix_label, peer_map in raw_lookup.items():
        if not isinstance(peer_map, dict):
            continue
        local_key = normalize_fix_label(local_fix_label)
        for peer_fix_label, tolerance_m in peer_map.items():
            try:
                lookup[(local_key, normalize_fix_label(peer_fix_label))] = float(tolerance_m)
            except (TypeError, ValueError):
                continue
    return lookup


def normalize_fix_label(fix_label: str | None) -> str:
    return str(fix_label or "UNKNOWN").strip().upper()


SAFETY_TOLERANCE_LOOKUP = load_safety_tolerance_lookup()


def estimate_accuracy_m(
    *,
    fix_label: str,
) -> float | None:
    fix_key = normalize_fix_label(fix_label)
    return DEFAULT_FIX_ACCURACY_M.get(fix_key)


def combined_accuracy_m(local_accuracy_m: float | None, peer_accuracy_m: float | None) -> float | None:
    if local_accuracy_m is None or peer_accuracy_m is None:
        return None
    return math.sqrt((local_accuracy_m ** 2) + (peer_accuracy_m ** 2))


def safety_tolerance_m(
    *,
    local_fix_label: str,
    peer_fix_label: str,
    local_accuracy_m: float | None,
    peer_accuracy_m: float | None,
) -> float | None:
    local_key = normalize_fix_label(local_fix_label)
    peer_key = normalize_fix_label(peer_fix_label)

    tolerance_m = SAFETY_TOLERANCE_LOOKUP.get((local_key, peer_key))
    if tolerance_m is not None:
        return tolerance_m

    tolerance_m = SAFETY_TOLERANCE_LOOKUP.get((peer_key, local_key))
    if tolerance_m is not None:
        return tolerance_m

    return combined_accuracy_m(local_accuracy_m, peer_accuracy_m)


def conservative_distance_m(distance_m: float | None, combined_accuracy_m_value: float | None) -> float | None:
    if distance_m is None:
        return None
    if combined_accuracy_m_value is None:
        return distance_m
    return max(0.0, distance_m - combined_accuracy_m_value)


def buffered_distance_m(
    center_distance_m: float | None,
    local_buffer_distance_m: float,
    peer_buffer_distance_m: float,
) -> float | None:
    if center_distance_m is None:
        return None
    return max(0.0, center_distance_m - local_buffer_distance_m - peer_buffer_distance_m)


def haversine_distance_m(
    lat1: float,
    lon1: float,
    lat2: float,
    lon2: float,
) -> float:
    lat1_rad = math.radians(lat1)
    lon1_rad = math.radians(lon1)
    lat2_rad = math.radians(lat2)
    lon2_rad = math.radians(lon2)

    dlat = lat2_rad - lat1_rad
    dlon = lon2_rad - lon1_rad
    a = math.sin(dlat / 2.0) ** 2 + math.cos(lat1_rad) * math.cos(lat2_rad) * math.sin(dlon / 2.0) ** 2
    c = 2.0 * math.atan2(math.sqrt(a), math.sqrt(1.0 - a))
    return EARTH_RADIUS_M * c


def snapshot_local_state() -> dict:
    with STATUS_LOCK:
        return {
            "latitude": STATUS.latitude,
            "longitude": STATUS.longitude,
            "altitude_m": STATUS.altitude_m,
            "fix_label": STATUS.fix_label,
            "fix_quality": STATUS.fix_quality,
        }


def build_peer_payload(device_id: str, peer_cfg: dict) -> dict:
    local = snapshot_local_state()
    accuracy_m = estimate_accuracy_m(
        fix_label=local["fix_label"],
    )
    update_local_horizontal_accuracy(accuracy_m)
    buffer_distance_m = float(peer_cfg.get("bufferDistanceM", 0.0))

    return {
        "schema": PEER_SCHEMA,
        "device_id": device_id,
        "sent_at": time.time(),
        "latitude": local["latitude"],
        "longitude": local["longitude"],
        "altitude_m": local["altitude_m"],
        "fix_label": local["fix_label"],
        "fix_quality": local["fix_quality"],
        "accuracy_m": accuracy_m,
        "buffer_distance_m": buffer_distance_m,
    }


def parse_peer_message(data: bytes) -> dict:
    payload = json.loads(data.decode("utf-8"))
    if payload.get("schema") != PEER_SCHEMA:
        raise ValueError("Unsupported peer schema")
    if "device_id" not in payload:
        raise ValueError("Missing device_id")
    return payload


def get_extra_targets(peer_cfg: dict) -> list[str]:
    raw_targets = peer_cfg.get("extraTargets", [])
    if not isinstance(raw_targets, list):
        return []
    return [str(target).strip() for target in raw_targets if str(target).strip()]


def build_peer_status_from_message(
    payload: dict,
    source_host: str,
    peer_cfg: dict,
) -> PeerStatus:
    now = time.time()
    local = snapshot_local_state()
    local_accuracy_m = estimate_accuracy_m(
        fix_label=local["fix_label"],
    )
    update_local_horizontal_accuracy(local_accuracy_m)

    peer_fix_label = str(payload.get("fix_label", "UNKNOWN"))
    peer_accuracy_m = payload.get("accuracy_m")
    if peer_accuracy_m is None:
        peer_accuracy_m = estimate_accuracy_m(
            fix_label=peer_fix_label,
        )
    else:
        peer_accuracy_m = float(peer_accuracy_m)

    local_buffer_distance_m = float(peer_cfg.get("bufferDistanceM", 0.0))
    peer_buffer_distance_m = float(payload.get("buffer_distance_m", 0.0))

    center_distance_m = None
    if None not in (local["latitude"], local["longitude"], payload.get("latitude"), payload.get("longitude")):
        center_distance_m = haversine_distance_m(
            float(local["latitude"]),
            float(local["longitude"]),
            float(payload["latitude"]),
            float(payload["longitude"]),
        )
    distance_m = buffered_distance_m(center_distance_m, local_buffer_distance_m, peer_buffer_distance_m)

    applied_tolerance_m = safety_tolerance_m(
        local_fix_label=local["fix_label"],
        peer_fix_label=peer_fix_label,
        local_accuracy_m=local_accuracy_m,
        peer_accuracy_m=peer_accuracy_m,
    )

    return PeerStatus(
        device_id=str(payload["device_id"]),
        latitude=payload.get("latitude"),
        longitude=payload.get("longitude"),
        altitude_m=payload.get("altitude_m"),
        fix_label=peer_fix_label,
        fix_quality=payload.get("fix_quality"),
        accuracy_m=peer_accuracy_m,
        sent_at=float(payload.get("sent_at", now)),
        received_at=now,
        distance_m=distance_m,
        combined_accuracy_m=applied_tolerance_m,
        conservative_distance_m=conservative_distance_m(distance_m, applied_tolerance_m),
        source_host=source_host,
        max_message_age_sec=float(peer_cfg.get("maxPeerMessageAgeSec", 2.0)),
    )


def peer_udp_loop(peer_cfg: dict, stop_event) -> None:
    device_id = str(peer_cfg.get("deviceId") or socket.gethostname())
    bind_host = str(peer_cfg.get("listenHost", ""))
    broadcast_host = str(peer_cfg.get("broadcastHost", "255.255.255.255"))
    extra_targets = get_extra_targets(peer_cfg)
    port = int(peer_cfg.get("port", 5005))
    recv_poll_timeout = float(peer_cfg.get("recvPollTimeoutSec", 0.02))
    broadcast_interval = float(peer_cfg.get("broadcastIntervalSec", 0.1))

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    sock.bind((bind_host, port))
    sock.settimeout(recv_poll_timeout)

    logging.info(
        "Peer UDP started device_id=%s bind=%s:%s broadcast=%s extra_targets=%s",
        device_id,
        bind_host or "*",
        port,
        broadcast_host,
        extra_targets,
    )

    next_broadcast_at = 0.0
    try:
        while not stop_event.is_set():
            now = time.time()
            if now >= next_broadcast_at:
                payload = build_peer_payload(device_id, peer_cfg)
                encoded = json.dumps(payload).encode("utf-8")
                sock.sendto(encoded, (broadcast_host, port))
                for target in extra_targets:
                    try:
                        sock.sendto(encoded, (target, port))
                    except OSError as exc:
                        logging.debug("Peer UDP send failed to %s:%s: %s", target, port, exc)
                update_peer_runtime_state(last_broadcast_at=payload["sent_at"], error=None)
                next_broadcast_at = now + broadcast_interval

            try:
                data, addr = sock.recvfrom(4096)
            except socket.timeout:
                continue

            try:
                payload = parse_peer_message(data)
                if str(payload["device_id"]) == device_id:
                    continue
                peer_status = build_peer_status_from_message(payload, addr[0], peer_cfg)
                upsert_peer_status(peer_status)
                update_peer_runtime_state(last_receive_at=peer_status.received_at, error=None)
            except Exception as exc:
                logging.debug("Peer UDP message ignored from %s: %s", addr[0], exc)
    except Exception as exc:
        update_peer_runtime_state(error=str(exc))
        logging.error("Peer UDP error: %s", exc)
    finally:
        sock.close()
        logging.info("Peer UDP stopped")
