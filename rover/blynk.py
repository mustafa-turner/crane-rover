from __future__ import annotations

import json
import logging
import ssl
import time
from dataclasses import dataclass

import paho.mqtt.client as mqtt

from rover.state import FIX_MODE_ENUM, NTRIP_STATUS_ENUM, STATUS, STATUS_LOCK


@dataclass
class SafetyViewSnapshot:
    rover_name: str
    rover_fix_label: str
    rover_correction_mode: str
    rover_ntrip_connected: bool
    rover_ntrip_locked_out: bool
    rover_ntrip_consecutive_failures: int
    rover_ntrip_status_detail: str
    nearest_peer_id: str
    nearest_peer_fix_label: str
    safe_distance_m: float | None
    raw_distance_m: float | None
    nearest_peer_accuracy_m: float | None
    nearest_peer_uncertainty_m: float | None
    threshold_m: float
    state: str
    peer_count: int
    fresh_peer_count: int
    updated_at: float


def get_safety_view_snapshot(rover_name: str = "", threshold_m: float = 25.0) -> SafetyViewSnapshot:
    with STATUS_LOCK:
        fix_label = STATUS.fix_label
        correction_mode = STATUS.correction_source_mode
        ntrip_connected = STATUS.ntrip_connected
        ntrip_locked_out = STATUS.ntrip_locked_out
        ntrip_consecutive_failures = STATUS.ntrip_consecutive_failures
        ntrip_lockout_reason = STATUS.ntrip_lockout_reason
        ntrip_last_error = STATUS.ntrip_last_error
        ntrip_last_response = STATUS.ntrip_last_response
        peers = list(STATUS.peers.values())

    now = time.time()
    fresh_peers = []
    for peer in peers:
        if peer.received_at is None:
            continue
        peer_age_sec = now - peer.received_at
        if peer.max_message_age_sec > 0 and peer_age_sec > peer.max_message_age_sec:
            continue
        fresh_peers.append(peer)

    nearest_peer = None
    peers_with_distance = [peer for peer in fresh_peers if peer.distance_m is not None]
    if peers_with_distance:
        nearest_peer = min(
            peers_with_distance,
            key=lambda peer: (
                peer.conservative_distance_m if peer.conservative_distance_m is not None else peer.distance_m
            ),
        )

    safe_distance_m = None
    raw_distance_m = None
    nearest_peer_accuracy_m = None
    nearest_peer_uncertainty_m = None
    nearest_peer_id = ""
    nearest_peer_fix_label = "UNKNOWN"
    state = "connecting"

    if nearest_peer is not None:
        safe_distance_m = nearest_peer.conservative_distance_m
        raw_distance_m = nearest_peer.distance_m
        nearest_peer_accuracy_m = nearest_peer.accuracy_m
        nearest_peer_uncertainty_m = nearest_peer.combined_accuracy_m
        nearest_peer_id = nearest_peer.device_id
        nearest_peer_fix_label = nearest_peer.fix_label
        if safe_distance_m is not None:
            state = "safe" if safe_distance_m > threshold_m else "danger"
    elif peers:
        state = "stale" if not fresh_peers else "connecting"

    return SafetyViewSnapshot(
        rover_name=rover_name,
        rover_fix_label=fix_label,
        rover_correction_mode=correction_mode,
        rover_ntrip_connected=ntrip_connected,
        rover_ntrip_locked_out=ntrip_locked_out,
        rover_ntrip_consecutive_failures=ntrip_consecutive_failures,
        rover_ntrip_status_detail=ntrip_lockout_reason or ntrip_last_error or ntrip_last_response or "",
        nearest_peer_id=nearest_peer_id,
        nearest_peer_fix_label=nearest_peer_fix_label,
        safe_distance_m=safe_distance_m,
        raw_distance_m=raw_distance_m,
        nearest_peer_accuracy_m=nearest_peer_accuracy_m,
        nearest_peer_uncertainty_m=nearest_peer_uncertainty_m,
        threshold_m=threshold_m,
        state=state,
        peer_count=len(peers),
        fresh_peer_count=len(fresh_peers),
        updated_at=now,
    )


def get_telemetry_payload(rover_name: str = "") -> dict:
    with STATUS_LOCK:
        lat = STATUS.latitude
        lon = STATUS.longitude
        alt = STATUS.altitude_m
        sats = STATUS.satellites
        hdop = STATUS.hdop
        fix_label = STATUS.fix_label
        correction_mode = STATUS.correction_source_mode
        ntrip_connected = STATUS.ntrip_connected
        last_rtcm_received_at = STATUS.last_rtcm_received_at
        battery_percent = STATUS.battery_percent
        battery_voltage_v = STATUS.battery_voltage_v
        battery_current_a = STATUS.battery_current_a
        battery_power_w = STATUS.battery_power_w
        battery_status = STATUS.battery_status
        battery_present = STATUS.battery_present
        local_horizontal_accuracy_m = STATUS.local_horizontal_accuracy_m

    now = time.time()
    rtcm_age_sec = 999.0
    if last_rtcm_received_at is not None:
        rtcm_age_sec = max(0.0, now - last_rtcm_received_at)

    safety = get_safety_view_snapshot(rover_name)

    payload = {
        "rover_name": rover_name,
        "latitude": lat if lat is not None else 0.0,
        "longitude": lon if lon is not None else 0.0,
        "altitude_m": alt if alt is not None else 0.0,
        "satellites": sats if sats is not None else 0,
        "hdop": hdop if hdop is not None else 99.0,
        "rtcm_age_sec": round(rtcm_age_sec, 1),
        "fix_mode": FIX_MODE_ENUM.get(fix_label, 0),
        "ntrip_status": NTRIP_STATUS_ENUM[ntrip_connected],
        "rtcm_source_mode": correction_mode,
        "battery_percent": round(battery_percent, 1) if battery_percent is not None else -1.0,
        "battery_voltage_v": round(battery_voltage_v, 3) if battery_voltage_v is not None else 0.0,
        "battery_current_a": round(battery_current_a, 3) if battery_current_a is not None else 0.0,
        "battery_power_w": round(battery_power_w, 3) if battery_power_w is not None else 0.0,
        "battery_status": battery_status,
        "battery_present": 1 if battery_present is True else 0 if battery_present is False else -1,
        "local_accuracy_m": round(local_horizontal_accuracy_m, 3) if local_horizontal_accuracy_m is not None else -1.0,
        "nearest_peer_distance_m": round(safety.raw_distance_m, 3) if safety.raw_distance_m is not None else -1.0,
        "nearest_peer_safe_distance_m": (
            round(safety.safe_distance_m, 3) if safety.safe_distance_m is not None else -1.0
        ),
        "nearest_peer_uncertainty_m": (
            round(safety.nearest_peer_uncertainty_m, 3) if safety.nearest_peer_uncertainty_m is not None else -1.0
        ),
        "nearest_peer_combined_accuracy_m": (
            round(safety.nearest_peer_uncertainty_m, 3) if safety.nearest_peer_uncertainty_m is not None else -1.0
        ),
        "nearest_peer_accuracy_m": (
            round(safety.nearest_peer_accuracy_m, 3) if safety.nearest_peer_accuracy_m is not None else -1.0
        ),
        "nearest_peer_fix_mode": FIX_MODE_ENUM.get(safety.nearest_peer_fix_label, 0),
        "nearest_peer_id": safety.nearest_peer_id,
    }
    if lat is not None and lon is not None:
        payload["position"] = [lon, lat]
    return payload


def publish_blynk_info(client: mqtt.Client, blynk_cfg: dict) -> None:
    info_payload = {
        "tmpl": blynk_cfg.get("templateId", ""),
        "ver": blynk_cfg.get("firmwareVersion", "0.1.0"),
        "build": time.strftime("%b %d %Y %H:%M:%S"),
        "type": blynk_cfg.get("templateId", ""),
        "rxbuff": 1024,
    }
    client.publish("info/mcu", json.dumps(info_payload), qos=0, retain=False)


def on_blynk_connect(client, userdata, flags, reason_code, properties=None):
    del flags, properties
    if reason_code == 0:
        logging.info("Blynk MQTT connected")
        client.subscribe("downlink/#", qos=0)
        publish_blynk_info(client, userdata["blynk_cfg"])
    else:
        logging.error("Blynk MQTT connect failed, reason_code=%s", reason_code)


def on_blynk_message(client, userdata, msg):
    del client, userdata
    if msg.topic == "downlink/redirect":
        redirect_uri = msg.payload.decode("utf-8", errors="ignore").strip()
        logging.warning("Blynk redirect received: %s", redirect_uri)
        logging.warning("Set broker in config to your regional endpoint if needed.")


def build_mqtt_client(username: str, password: str) -> mqtt.Client:
    client = mqtt.Client(
        callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
        client_id="",
        clean_session=True,
    )
    if username or password:
        client.username_pw_set(username, password)
    return client


def mqtt_publish_loop(
    mqtt_cfg: dict,
    stop_event,
    *,
    log_name: str,
    topic: str,
    username: str,
    password: str,
    on_connect=None,
    on_message=None,
    user_data: dict | None = None,
) -> None:
    broker = str(mqtt_cfg.get("broker", ""))
    port = int(mqtt_cfg.get("port", 1883))
    keepalive = int(mqtt_cfg.get("keepaliveSec", 45))
    publish_interval = float(mqtt_cfg.get("publishIntervalSec", 1.0))
    rover_name = str(mqtt_cfg.get("roverName", ""))
    use_tls = port == 8883

    client = build_mqtt_client(username, password)
    if user_data is not None:
        client.user_data_set(user_data)
    if on_connect is not None:
        client.on_connect = on_connect
    if on_message is not None:
        client.on_message = on_message

    if use_tls:
        client.tls_set(cert_reqs=ssl.CERT_REQUIRED)
        client.tls_insecure_set(False)

    while not stop_event.is_set():
        try:
            logging.info(
                "Connecting %s MQTT to %s:%s (TLS=%s)",
                log_name,
                broker,
                port,
                "on" if use_tls else "off",
            )
            client.connect(broker, port, keepalive=keepalive)
            client.loop_start()

            while not stop_event.is_set():
                payload = get_telemetry_payload(rover_name)
                client.publish(topic, json.dumps(payload), qos=0, retain=False)
                logging.debug("Published to %s MQTT topic %s: %s", log_name, topic, payload)
                stop_event.wait(publish_interval)
            break
        except Exception as exc:
            logging.error("%s MQTT error: %s", log_name, exc)
            stop_event.wait(5)
        finally:
            try:
                client.loop_stop()
            except Exception:
                pass
            try:
                client.disconnect()
            except Exception:
                pass


def blynk_loop(blynk_cfg: dict, stop_event) -> None:
    mqtt_publish_loop(
        blynk_cfg,
        stop_event,
        log_name="Blynk",
        topic="batch_ds",
        username=str(blynk_cfg.get("username", "device")),
        password=str(blynk_cfg["authToken"]),
        on_connect=on_blynk_connect,
        on_message=on_blynk_message,
        user_data={"blynk_cfg": blynk_cfg},
    )


def mqtt_loop(mqtt_cfg: dict, stop_event) -> None:
    mqtt_publish_loop(
        mqtt_cfg,
        stop_event,
        log_name="Generic",
        topic=str(mqtt_cfg.get("topic", "batch_ds")),
        username=str(mqtt_cfg.get("username", "")),
        password=str(mqtt_cfg.get("password", "")),
    )
