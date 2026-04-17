from __future__ import annotations

import base64
import logging
import socket
import time
from typing import Optional

from rover.state import (
    STATUS,
    STATUS_LOCK,
    RtcmStreamInspector,
    get_latest_gga,
    update_status_from_rtcm,
)


def build_ntrip_request(
    host: str,
    port: int,
    mountpoint: str,
    username: str,
    password: str,
) -> bytes:
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


def send_gga_to_caster(sock: socket.socket) -> bool:
    gga = get_latest_gga()
    if not gga:
        logging.debug("No GGA available yet to send to caster")
        return False

    payload = (gga + "\r\n").encode("ascii", errors="ignore")
    sock.sendall(payload)
    logging.info("Sent GGA to caster: %s", gga)
    return True


def ntrip_loop(ser, ntrip_cfg: dict, stop_event) -> None:
    host = ntrip_cfg["host"]
    port = int(ntrip_cfg.get("port", 2101))
    mountpoint = ntrip_cfg["mountpoint"]
    username = ntrip_cfg["username"]
    password = ntrip_cfg["password"]
    connect_timeout = int(ntrip_cfg.get("connectTimeoutSec", 10))
    read_timeout = int(ntrip_cfg.get("readTimeoutSec", 15))
    reconnect_delay = int(ntrip_cfg.get("reconnectDelaySec", 5))
    chunk_size = int(ntrip_cfg.get("chunkSize", 1024))
    gga_forward_enabled = bool(ntrip_cfg.get("ggaForwardEnabled", True))
    gga_forward_interval = int(ntrip_cfg.get("ggaForwardIntervalSec", 5))
    recv_poll_timeout = float(ntrip_cfg.get("recvPollTimeoutSec", 1.0))
    rtcm_log_interval = int(ntrip_cfg.get("rtcmLogIntervalSec", 10))

    logging.info("NTRIP loop started for %s:%s/%s", host, port, mountpoint)
    inspector = RtcmStreamInspector()
    last_rtcm_log_at = 0.0

    while not stop_event.is_set():
        sock: Optional[socket.socket] = None
        try:
            logging.info("Connecting to NTRIP caster %s:%s mountpoint=%s", host, port, mountpoint)
            sock = socket.create_connection((host, port), timeout=connect_timeout)
            sock.settimeout(recv_poll_timeout)
            sock.sendall(build_ntrip_request(host, port, mountpoint, username, password))

            header = read_http_header(sock)
            validate_ntrip_response(header)

            with STATUS_LOCK:
                STATUS.ntrip_connected = True
                STATUS.ntrip_last_error = None

            last_rtcm_data_at = time.time()
            last_gga_sent_at = 0.0

            if gga_forward_enabled:
                try:
                    if send_gga_to_caster(sock):
                        last_gga_sent_at = time.time()
                except Exception as exc:
                    logging.warning("Initial GGA send failed: %s", exc)

            while not stop_event.is_set():
                now = time.time()
                if gga_forward_enabled and (now - last_gga_sent_at >= gga_forward_interval):
                    try:
                        if send_gga_to_caster(sock):
                            last_gga_sent_at = time.time()
                    except Exception as exc:
                        raise ConnectionError(f"GGA send failed: {exc}") from exc

                try:
                    data = sock.recv(chunk_size)
                    logging.debug("Received %d RTCM bytes", len(data))
                    if not data:
                        raise ConnectionError("NTRIP connection closed by server")

                    ser.write(data)
                    ser.flush()
                    rtcm_types = inspector.feed(data)

                    now = time.time()
                    last_rtcm_data_at = now
                    with STATUS_LOCK:
                        STATUS.last_rtcm_received_at = now

                    update_status_from_rtcm(rtcm_types, len(data), inspector)
                    if rtcm_types and (now - last_rtcm_log_at >= rtcm_log_interval):
                        logging.info("Recent RTCM types: %s", inspector.describe_recent())
                        last_rtcm_log_at = now
                except socket.timeout:
                    if time.time() - last_rtcm_data_at >= read_timeout:
                        raise TimeoutError(f"No RTCM data received for {read_timeout} seconds")
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
