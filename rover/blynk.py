from __future__ import annotations

import json
import logging
import ssl
import time

import paho.mqtt.client as mqtt

from rover.state import FIX_MODE_ENUM, NTRIP_STATUS_ENUM, STATUS, STATUS_LOCK


def get_blynk_payload() -> dict:
    with STATUS_LOCK:
        lat = STATUS.latitude
        lon = STATUS.longitude
        alt = STATUS.altitude_m
        sats = STATUS.satellites
        hdop = STATUS.hdop
        fix_label = STATUS.fix_label
        ntrip_connected = STATUS.ntrip_connected
        last_rtcm_received_at = STATUS.last_rtcm_received_at

    now = time.time()
    rtcm_age_sec = 999.0
    if last_rtcm_received_at is not None:
        rtcm_age_sec = max(0.0, now - last_rtcm_received_at)

    payload = {
        "latitude": lat if lat is not None else 0.0,
        "longitude": lon if lon is not None else 0.0,
        "altitude_m": alt if alt is not None else 0.0,
        "satellites": sats if sats is not None else 0,
        "hdop": hdop if hdop is not None else 99.0,
        "rtcm_age_sec": round(rtcm_age_sec, 1),
        "fix_mode": FIX_MODE_ENUM.get(fix_label, 0),
        "ntrip_status": NTRIP_STATUS_ENUM[ntrip_connected],
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


def blynk_loop(blynk_cfg: dict, stop_event) -> None:
    broker = blynk_cfg.get("broker", "blynk.cloud")
    port = int(blynk_cfg.get("port", 8883))
    username = blynk_cfg.get("username", "device")
    password = blynk_cfg["authToken"]
    keepalive = int(blynk_cfg.get("keepaliveSec", 45))
    publish_interval = int(blynk_cfg.get("publishIntervalSec", 2))
    use_tls = bool(blynk_cfg.get("useTls", True))

    client = mqtt.Client(
        callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
        client_id="",
        clean_session=True,
    )
    client.username_pw_set(username, password)
    client.user_data_set({"blynk_cfg": blynk_cfg})
    client.on_connect = on_blynk_connect
    client.on_message = on_blynk_message

    if use_tls:
        client.tls_set(cert_reqs=ssl.CERT_REQUIRED)
        client.tls_insecure_set(False)

    while not stop_event.is_set():
        try:
            logging.info("Connecting to Blynk MQTT %s:%s", broker, port)
            client.connect(broker, port, keepalive=keepalive)
            client.loop_start()

            while not stop_event.is_set():
                payload = get_blynk_payload()
                client.publish("batch_ds", json.dumps(payload), qos=0, retain=False)
                logging.debug("Published to Blynk: %s", payload)
                stop_event.wait(publish_interval)
            break
        except Exception as exc:
            logging.error("Blynk MQTT error: %s", exc)
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
