import base64
import logging
import socket
import sys
import threading
import time
from dataclasses import dataclass
from typing import Optional

import serial
import yaml
from pynmeagps import NMEAReader


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


def build_ntrip_request(host: str, mountpoint: str, username: str, password: str) -> bytes:
    credentials = f"{username}:{password}".encode("utf-8")
    auth_b64 = base64.b64encode(credentials).decode("ascii")

    request = (
        f"GET /{mountpoint} HTTP/1.0\r\n"
        f"Host: {host}\r\n"
        f"User-Agent: NTRIP PythonClient/1.0\r\n"
        f"Authorization: Basic {auth_b64}\r\n"
        f"Accept: */*\r\n"
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
        pdop = getattr(parsed, "PDOP", None)
        hdop = getattr(parsed, "HDOP", None)
        if hdop is not None:
            STATUS.hdop = hdop
        STATUS.last_nmea_at = time.time()
    if pdop is not None:
        logging.debug("PDOP: %s", pdop)


def nmea_reader_loop(ser: serial.Serial, stop_event: threading.Event) -> None:
    logging.info("NMEA reader started on %s", ser.port)
    reader = NMEAReader(ser, validate=0)

    while not stop_event.is_set():
        try:
            raw_data, parsed_data = reader.read()
            if raw_data is None:
                continue

            if parsed_data is None:
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
            sock = socket.create_connection((host, port), timeout=connect_timeout)
            sock.settimeout(read_timeout)

            request = build_ntrip_request(host, mountpoint, username, password)
            sock.sendall(request)

            header = read_http_header(sock)
            validate_ntrip_response(header)

            with STATUS_LOCK:
                STATUS.ntrip_connected = True
                STATUS.ntrip_last_error = None

            while not stop_event.is_set():
                data = sock.recv(chunk_size)
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


def main() -> int:
    config_path = sys.argv[1] if len(sys.argv) > 1 else "config.yaml"
    config = load_config(config_path)

    setup_logging(config.get("logging", {}).get("level", "INFO"))

    serial_cfg = config["serial"]
    status_cfg = config.get("status", {})

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

    nmea_thread.start()
    ntrip_thread.start()
    printer_thread.start()

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
        ser.close()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())