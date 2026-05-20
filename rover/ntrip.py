from __future__ import annotations

import base64
import logging
import socket
import time
from typing import Optional

from rover.rtcm_common import process_rtcm_data, set_correction_runtime_state
from rover.state import (
    RtcmStreamInspector,
    get_latest_gga,
    STATUS,
    STATUS_LOCK,
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


def read_ntrip_handshake(sock: socket.socket) -> tuple[bytes, bytes]:
    buffer = bytearray()

    while True:
        chunk = sock.recv(1024)
        if not chunk:
            break
        buffer.extend(chunk)

        header_end = buffer.find(b"\r\n\r\n")
        if header_end >= 0:
            body_start = header_end + 4
            return bytes(buffer[:body_start]), bytes(buffer[body_start:])

        header_end = buffer.find(b"\n\n")
        if header_end >= 0:
            body_start = header_end + 2
            return bytes(buffer[:body_start]), bytes(buffer[body_start:])

        if buffer and buffer[0] == 0xD3:
            logging.info("NTRIP caster started RTCM stream without a standard response header")
            return b"", bytes(buffer)

    return bytes(buffer), b""


def validate_ntrip_response(header: bytes) -> None:
    if not header:
        logging.info("NTRIP stream accepted without response header")
        return

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


def ntrip_loop(ser, ntrip_cfg: dict, rtcm_cfg: dict, stop_event) -> None:
    host = ntrip_cfg["host"]
    port = int(ntrip_cfg.get("port", 2101))
    mountpoint = str(ntrip_cfg["mountpoint"]).lstrip("/")
    username = ntrip_cfg["username"]
    password = ntrip_cfg["password"]
    connect_timeout = int(rtcm_cfg.get("connectTimeoutSec", 10))
    read_timeout = int(rtcm_cfg.get("readTimeoutSec", 15))
    reconnect_delay = int(rtcm_cfg.get("reconnectDelaySec", 5))
    chunk_size = int(rtcm_cfg.get("chunkSize", 1024))
    gga_forward_enabled = bool(ntrip_cfg.get("ggaForwardEnabled", True))
    gga_forward_interval = int(ntrip_cfg.get("ggaForwardIntervalSec", 5))
    recv_poll_timeout = float(rtcm_cfg.get("recvPollTimeoutSec", 1.0))
    rtcm_log_interval = int(rtcm_cfg.get("rtcmLogIntervalSec", 10))

    logging.info("NTRIP loop started for %s:%s/%s", host, port, mountpoint)
    inspector = RtcmStreamInspector()
    last_rtcm_log_at = 0.0

    while not stop_event.is_set():
        sock: Optional[socket.socket] = None
        try:
            logging.info("Connecting to NTRIP caster %s:%s mountpoint=%s", host, port, mountpoint)
            sock = socket.create_connection((host, port), timeout=connect_timeout)
            logging.info("TCP connected to NTRIP caster %s:%s", host, port)
            sock.sendall(build_ntrip_request(host, port, mountpoint, username, password))
            logging.info("Sent NTRIP request for mountpoint=%s", mountpoint)

            # Keep a longer timeout during the initial NTRIP handshake.
            sock.settimeout(connect_timeout)
            logging.info("Waiting for NTRIP response header")
            header, initial_data = read_ntrip_handshake(sock)
            logging.info(
                "Received NTRIP handshake header=%d bytes initial_data=%d bytes",
                len(header),
                len(initial_data),
            )
            validate_ntrip_response(header)
            response_line = header.decode("latin1", errors="ignore").splitlines()[0] if header else ""
            sock.settimeout(recv_poll_timeout)

            set_correction_runtime_state(
                mode="ntrip",
                connected=True,
                last_error=None,
                last_response=response_line,
            )

            last_rtcm_data_at = time.time()
            last_gga_sent_at = 0.0
            has_sent_gga = False
            waiting_for_gga_logged = False

            if gga_forward_enabled:
                try:
                    if send_gga_to_caster(sock):
                        last_gga_sent_at = time.time()
                        has_sent_gga = True
                        with STATUS_LOCK:
                            STATUS.last_gga_sent_at = last_gga_sent_at
                except Exception as exc:
                    logging.warning("Initial GGA send failed: %s", exc)

            if initial_data:
                logging.debug("Processing %d initial RTCM bytes from handshake", len(initial_data))
                last_rtcm_log_at = process_rtcm_data(
                    ser=ser,
                    data=initial_data,
                    inspector=inspector,
                    rtcm_log_interval=rtcm_log_interval,
                    last_rtcm_log_at=last_rtcm_log_at,
                )
                last_rtcm_data_at = time.time()

            while not stop_event.is_set():
                now = time.time()
                if gga_forward_enabled and (now - last_gga_sent_at >= gga_forward_interval):
                    try:
                        if send_gga_to_caster(sock):
                            last_gga_sent_at = time.time()
                            has_sent_gga = True
                            waiting_for_gga_logged = False
                            with STATUS_LOCK:
                                STATUS.last_gga_sent_at = last_gga_sent_at
                    except Exception as exc:
                        raise ConnectionError(f"GGA send failed: {exc}") from exc

                try:
                    data = sock.recv(chunk_size)
                    logging.debug("Received %d RTCM bytes", len(data))
                    if not data:
                        raise ConnectionError("NTRIP connection closed by server")

                    last_rtcm_log_at = process_rtcm_data(
                        ser=ser,
                        data=data,
                        inspector=inspector,
                        rtcm_log_interval=rtcm_log_interval,
                        last_rtcm_log_at=last_rtcm_log_at,
                    )
                    last_rtcm_data_at = time.time()
                except socket.timeout:
                    if gga_forward_enabled and not has_sent_gga:
                        if not waiting_for_gga_logged:
                            logging.info("Connected to NTRIP caster and waiting for first GGA before enforcing RTCM timeout")
                            waiting_for_gga_logged = True
                        continue
                    if time.time() - last_rtcm_data_at >= read_timeout:
                        gga_age_text = "unknown"
                        with STATUS_LOCK:
                            if STATUS.last_gga_at is not None:
                                gga_age_text = f"{time.time() - STATUS.last_gga_at:.1f}s"
                        raise TimeoutError(
                            f"No RTCM data received for {read_timeout} seconds "
                            f"(mountpoint={mountpoint}, gga_forward={gga_forward_enabled}, "
                            f"gga_sent={has_sent_gga}, latest_gga_age={gga_age_text})"
                        )
        except Exception as exc:
            set_correction_runtime_state(mode="ntrip", connected=False, last_error=str(exc))
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

    set_correction_runtime_state(mode="ntrip", connected=False)
    logging.info("NTRIP loop stopped")
