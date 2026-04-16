import base64
import logging
import socket
import sys
import threading
import time
from typing import Optional

import serial
import yaml


def load_config(path: str) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def setup_logging(level: str) -> None:
    numeric_level = getattr(logging, level.upper(), logging.INFO)
    logging.basicConfig(
        level=numeric_level,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )


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


def nmea_reader_loop(ser: serial.Serial, stop_event: threading.Event) -> None:
    logging.info("NMEA reader started on %s", ser.port)

    while not stop_event.is_set():
        try:
            raw = ser.readline()
            if not raw:
                continue

            try:
                line = raw.decode("ascii", errors="ignore").strip()
            except Exception:
                line = ""

            if line.startswith("$"):
                print(line, flush=True)

        except serial.SerialException as exc:
            logging.error("Serial read error: %s", exc)
            time.sleep(1)
        except Exception as exc:
            logging.exception("Unexpected error in NMEA reader: %s", exc)
            time.sleep(1)

    logging.info("NMEA reader stopped")


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

            while not stop_event.is_set():
                data = sock.recv(chunk_size)
                if not data:
                    raise ConnectionError("NTRIP connection closed by server")

                ser.write(data)
                ser.flush()

        except Exception as exc:
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

    logging.info("NTRIP loop stopped")


def main() -> int:
    config_path = sys.argv[1] if len(sys.argv) > 1 else "config.yaml"
    config = load_config(config_path)

    setup_logging(config.get("logging", {}).get("level", "INFO"))

    ser = open_serial(config["serial"])
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

    nmea_thread.start()
    ntrip_thread.start()

    logging.info("Stage 1 rover started. Printing NMEA and feeding RTCM from NTRIP.")

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        logging.info("Stopping...")
    finally:
        stop_event.set()
        nmea_thread.join(timeout=2)
        ntrip_thread.join(timeout=2)
        ser.close()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())