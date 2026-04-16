import base64
import json
import logging
import socket
import ssl
import sys
import threading
import time
from dataclasses import dataclass
from typing import Optional

import paho.mqtt.client as mqtt
import serial
import yaml
from pynmeagps import NMEAReader


FIX_MODE_ENUM = {
    "NO FIX": 0,
    "GNSS FIX": 1,
    "DGPS": 2,
    "RTK FLOAT": 3,
    "RTK FIXED": 4,
}

NTRIP_STATUS_ENUM = {
    False: 0,  # DISCONNECTED
    True: 1,   # CONNECTED
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


STATUS_LOCK = threading.Lock()
STATUS = RoverStatus()


def load_config(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def setup_logging(level: str) -> None:
    numeric_level = getattr(logging, level.upper(), logging.INFO)
    logging.basicConfig(
        level=numeric_level,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )


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


def build_ntrip_request(host: str, port: int, mountpoint: str, username: str, password: str) -> bytes:
    credentials = f"{username}:{password}".encode("utf-8")
    auth_b64 = base64.b64encode(credentials).decode("ascii")

    request = (
        f"GET /{mountpoint} HTTP/1.1\r\n"
        f"Host: {host}:{port}\r\n"
        f"Ntrip-Version: Ntrip/2.0\r\n"
        f"User-Agent: NTRIP PythonClient/1.0\r\n"
        f"Authorization: Basic {auth_b64}\r\n"
        f"Connection: close\r\n"
        f"\r\n"
    )
    return request.encode("utf-8")


def open_serial(serial_cfg: dict) -> serial.Serial:
    return serial.Serial(
        port=serial_cfg["port"],
        baudrate=serial_cfg.get("baudrate", 115200),
        timeout=1,
    )


def read_http_header(sock: socket.socket) -> bytes:
    header = b""
    while b"\r\n\r\n" not in header:
        chunk = sock.recv(1)
        if not chunk:
            break
        header += chunk
    return header


def validate_ntrip_response(header: bytes) -> None:
    header_text = header.decode("latin1", errors="ignore")
    logging.debug("NTRIP response header:\n%s", header_text)

    first_line = header_text.splitlines()[0] if header_text.splitlines() else ""

    if "200 OK" in first_line or "ICY 200 OK" in first_line:
        logging.info("NTRIP connected successfully: %s", first_line)
        return

    raise RuntimeError(f"NTRIP server rejected connection: {first_line}")


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


def nmea_reader_loop(ser: serial.Serial, stop_event: threading.Event) -> None:
    logging.info("NMEA reader started on %s", ser.port)
    reader = NMEAReader(ser, validate=0)

    while not stop_event.is_set():
        try:
            raw_data, parsed_data = reader.read()
            if raw_data is None or parsed_data is None:
                continue

            msg_id = getattr(parsed_data, "msgID", "")

            if msg_id == "GGA":
                update_status_from_gga(parsed_data)
            elif msg_id == "GSA":
                update_status_from_gsa(parsed_data)

        except serial.SerialException as exc:
            logging.error("Serial read error: %s", exc)
            time.sleep(1)
        except Exception as exc:
            logging.debug("NMEA parse/read error: %s", exc)
            time.sleep(0.05)

    logging.info("NMEA reader stopped")


def ntrip_loop(ser: serial.Serial, ntrip_cfg: dict, stop_event: threading.Event) -> None:
    host = ntrip_cfg["host"]
    port = int(ntrip_cfg.get("port", 2101))
    mountpoint = ntrip_cfg["mountpoint"]
    username = ntrip_cfg["username"]
    password = ntrip_cfg["password"]
    connect_timeout = int(ntrip_cfg.get("connectTimeoutSec", 10))
    read_timeout = int(ntrip_cfg.get("readTimeoutSec", 15))
    reconnect_delay = int(ntrip_cfg.get("reconnectDelaySec", 5))
    chunk_size = int(ntrip_cfg.get("chunkSize", 1024))

    logging.info("NTRIP loop started for %s:%s/%s", host, port, mountpoint)

    while not stop_event.is_set():
        sock: Optional[socket.socket] = None
        try:
            logging.info("Connecting to NTRIP caster %s:%s mountpoint=%s", host, port, mountpoint)

            sock = socket.create_connection((host, port), timeout=connect_timeout)
            sock.settimeout(read_timeout)

            request = build_ntrip_request(host, port, mountpoint, username, password)
            sock.sendall(request)

            header = read_http_header(sock)
            validate_ntrip_response(header)

            with STATUS_LOCK:
                STATUS.ntrip_connected = True
                STATUS.ntrip_last_error = None

            while not stop_event.is_set():
                data = sock.recv(chunk_size)
                logging.debug("Received %d RTCM bytes", len(data))

                if not data:
                    raise ConnectionError("NTRIP connection closed by server")

                ser.write(data)
                ser.flush()

                with STATUS_LOCK:
                    STATUS.last_rtcm_received_at = time.time()

        except Exception as exc:
            with STATUS_LOCK:
                STATUS.ntrip_connected = False
                STATUS.ntrip_last_error = str(exc)

            logging.error("NTRIP error: %s", exc)
            if not stop_event.is_set():
                logging.info("Reconnecting NTRIP in %s seconds...", reconnect_delay)
                time.sleep(reconnect_delay)
        finally:
            if sock is not None:
                try:
                    sock.close()
                except Exception:
                    pass

    with STATUS_LOCK:
        STATUS.ntrip_connected = False
    logging.info("NTRIP loop stopped")


def status_printer_loop(interval_sec: int, stop_event: threading.Event) -> None:
    while not stop_event.is_set():
        with STATUS_LOCK:
            lat = STATUS.latitude
            lon = STATUS.longitude
            alt = STATUS.altitude_m
            sats = STATUS.satellites
            hdop = STATUS.hdop
            fix_label = STATUS.fix_label
            ntrip_connected = STATUS.ntrip_connected
            ntrip_last_error = STATUS.ntrip_last_error
            last_rtcm_received_at = STATUS.last_rtcm_received_at
            last_nmea_at = STATUS.last_nmea_at

        now = time.time()

        rtcm_age = "-"
        if last_rtcm_received_at is not None:
            rtcm_age = f"{now - last_rtcm_received_at:.1f}s"

        nmea_age = "-"
        if last_nmea_at is not None:
            nmea_age = f"{now - last_nmea_at:.1f}s"

        ntrip_text = "CONNECTED" if ntrip_connected else "DISCONNECTED"

        print("\n=== ROVER STATUS ===", flush=True)
        print(f"Latitude        : {fmt(lat, 8)}", flush=True)
        print(f"Longitude       : {fmt(lon, 8)}", flush=True)
        print(f"Altitude (m)    : {fmt(alt, 3)}", flush=True)
        print(f"Satellites      : {fmt_int(sats)}", flush=True)
        print(f"HDOP            : {fmt(hdop, 2)}", flush=True)
        print(f"Fix / RTK Mode  : {fix_label}", flush=True)
        print(f"NTRIP Status    : {ntrip_text}", flush=True)
        print(f"Last RTCM Age   : {rtcm_age}", flush=True)
        print(f"Last NMEA Age   : {nmea_age}", flush=True)

        if ntrip_last_error:
            print(f"NTRIP Error     : {ntrip_last_error}", flush=True)

        print("====================\n", flush=True)

        stop_event.wait(interval_sec)


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
    if reason_code == 0:
        logging.info("Blynk MQTT connected")
        client.subscribe("downlink/#", qos=0)
        publish_blynk_info(client, userdata["blynk_cfg"])
    else:
        logging.error("Blynk MQTT connect failed, reason_code=%s", reason_code)


def on_blynk_message(client, userdata, msg):
    if msg.topic == "downlink/redirect":
        redirect_uri = msg.payload.decode("utf-8", errors="ignore").strip()
        logging.warning("Blynk redirect received: %s", redirect_uri)
        logging.warning("Set broker in config to your regional endpoint if needed.")


def blynk_loop(blynk_cfg: dict, stop_event: threading.Event) -> None:
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


def main() -> int:
    config_path = sys.argv[1] if len(sys.argv) > 1 else "config.yaml"
    config = load_config(config_path)

    setup_logging(config.get("logging", {}).get("level", "INFO"))

    serial_cfg = config["serial"]
    status_cfg = config.get("status", {})
    blynk_cfg = config.get("blynk", {})

    ser = open_serial(serial_cfg)
    stop_event = threading.Event()

    nmea_thread = threading.Thread(
        target=nmea_reader_loop,
        args=(ser, stop_event),
        daemon=True,
    )
    ntrip_thread = threading.Thread(
        target=ntrip_loop,
        args=(ser, config["ntrip"], stop_event),
        daemon=True,
    )
    printer_thread = threading.Thread(
        target=status_printer_loop,
        args=(int(status_cfg.get("printIntervalSec", 2)), stop_event),
        daemon=True,
    )

    blynk_thread = None
    if blynk_cfg.get("enabled", False):
        blynk_thread = threading.Thread(
            target=blynk_loop,
            args=(blynk_cfg, stop_event),
            daemon=True,
        )

    nmea_thread.start()
    ntrip_thread.start()
    printer_thread.start()
    if blynk_thread is not None:
        blynk_thread.start()

    logging.info("Stage 1 rover started.")

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        logging.info("Stopping...")
    finally:
        stop_event.set()
        nmea_thread.join(timeout=2)
        ntrip_thread.join(timeout=2)
        printer_thread.join(timeout=2)
        if blynk_thread is not None:
            blynk_thread.join(timeout=2)
        ser.close()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
