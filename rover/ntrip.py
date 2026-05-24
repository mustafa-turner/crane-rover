from __future__ import annotations

import base64
import logging
import socket
import time
from typing import Optional

from rover.rtcm_common import process_rtcm_data, set_correction_runtime_state
from rover.state import (
    RtcmStreamInspector,
    consume_ntrip_reconnect_request,
    get_latest_gga,
    STATUS,
    STATUS_LOCK,
)


INITIAL_RECONNECT_DELAY_SEC = 10
RECONNECT_BACKOFF_SEQUENCE_SEC = (10, 20, 40, 80, 120)
MAX_RECONNECT_DELAY_SEC = 120
STABLE_RTCM_RESET_SEC = 60
RTCM_STALE_RECONNECT_SEC = 30
GNSS_FIX_QUALITY_RECONNECT_SEC = 60
NTRIP_ALERT_THRESHOLD = 5
DEFAULT_GGA_FORWARD_INTERVAL_SEC = 15


class NtripPermanentError(RuntimeError):
    pass


class NtripReconnectTrigger(RuntimeError):
    def __init__(self, reason_code: str, message: str) -> None:
        super().__init__(message)
        self.reason_code = reason_code


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
    upper_header = header_text.upper()
    upper_first_line = first_line.upper()

    if "SOURCETABLE" in upper_header:
        raise NtripPermanentError("NTRIP caster returned SOURCETABLE instead of an RTCM stream")

    if "200 OK" in upper_first_line or "ICY 200 OK" in upper_first_line:
        logging.info("NTRIP connected successfully: %s", first_line)
        return

    if any(code in upper_first_line for code in ("401", "403", "404")):
        raise NtripPermanentError(f"NTRIP server rejected connection: {first_line}")

    raise NtripPermanentError(f"NTRIP server rejected connection: {first_line}")


def reconnect_delay_for_failure_count(consecutive_failures: int) -> int:
    if consecutive_failures <= 0:
        return INITIAL_RECONNECT_DELAY_SEC
    index = min(consecutive_failures, len(RECONNECT_BACKOFF_SEQUENCE_SEC)) - 1
    return min(RECONNECT_BACKOFF_SEQUENCE_SEC[index], MAX_RECONNECT_DELAY_SEC)


def send_gga_to_caster(sock: socket.socket) -> bool:
    gga = get_latest_gga()
    if not gga:
        logging.debug("No GGA available yet to send to caster")
        return False

    payload = (gga + "\r\n").encode("ascii", errors="ignore")
    sock.sendall(payload)
    logging.info("Sent GGA to caster: %s", gga)
    return True


def update_ntrip_alert_state(
    *,
    consecutive_failures: int,
    reason: str | None = None,
    clear: bool = False,
) -> None:
    with STATUS_LOCK:
        if clear:
            STATUS.ntrip_alert_active = False
            STATUS.ntrip_alert_reason = None
            return

        crossed_threshold = consecutive_failures >= NTRIP_ALERT_THRESHOLD and not STATUS.ntrip_alert_active
        if crossed_threshold:
            STATUS.ntrip_alert_active = True
            STATUS.ntrip_alert_seq += 1
        if STATUS.ntrip_alert_active:
            STATUS.ntrip_alert_reason = reason


def ntrip_loop(ser, ntrip_cfg: dict, rtcm_cfg: dict, stop_event) -> None:
    host = ntrip_cfg["host"]
    port = int(ntrip_cfg.get("port", 2101))
    mountpoint = str(ntrip_cfg["mountpoint"]).lstrip("/")
    username = ntrip_cfg["username"]
    password = ntrip_cfg["password"]
    connect_timeout = int(rtcm_cfg.get("connectTimeoutSec", 10))
    chunk_size = int(rtcm_cfg.get("chunkSize", 1024))
    gga_forward_enabled = bool(ntrip_cfg.get("ggaForwardEnabled", True))
    gga_forward_interval = int(ntrip_cfg.get("ggaForwardIntervalSec", DEFAULT_GGA_FORWARD_INTERVAL_SEC))
    recv_poll_timeout = float(rtcm_cfg.get("recvPollTimeoutSec", 1.0))
    rtcm_log_interval = int(rtcm_cfg.get("rtcmLogIntervalSec", 10))

    logging.info("NTRIP loop started for %s:%s/%s", host, port, mountpoint)
    inspector = RtcmStreamInspector()
    last_rtcm_log_at = 0.0
    consecutive_failures = 0
    recovery_reason_pending: str | None = None

    def wait_for_manual_reconnect(lockout_reason: str) -> bool:
        set_correction_runtime_state(
            mode="ntrip",
            connected=False,
            last_error=lockout_reason,
            consecutive_failures=consecutive_failures,
            locked_out=True,
            lockout_reason=lockout_reason,
        )
        logging.error("%s", lockout_reason)

        while not stop_event.is_set():
            if consume_ntrip_reconnect_request():
                return True
            stop_event.wait(0.2)
        return False

    def record_failure(message: str) -> int:
        nonlocal consecutive_failures
        consecutive_failures += 1
        set_correction_runtime_state(
            mode="ntrip",
            connected=False,
            last_error=message,
            consecutive_failures=consecutive_failures,
            locked_out=False,
        )
        update_ntrip_alert_state(consecutive_failures=consecutive_failures, reason=message)
        return consecutive_failures

    def clear_failure_state(response_line: str) -> None:
        nonlocal consecutive_failures, recovery_reason_pending
        consecutive_failures = 0
        recovery_reason_pending = None
        set_correction_runtime_state(
            mode="ntrip",
            connected=True,
            last_error=None,
            last_response=response_line,
            consecutive_failures=0,
            locked_out=False,
            lockout_reason=None,
        )
        update_ntrip_alert_state(consecutive_failures=0, clear=True)

    def evaluate_reconnect_health(
        *,
        now: float,
        last_rtcm_data_at: float,
        quality_one_started_at: float | None,
    ) -> float | None:
        rtcm_age = now - last_rtcm_data_at
        if rtcm_age > RTCM_STALE_RECONNECT_SEC:
            raise NtripReconnectTrigger(
                "rtcm_stale",
                f"RTCM age frozen for {rtcm_age:.1f} seconds; reconnecting NTRIP stream",
            )

        with STATUS_LOCK:
            fix_quality = STATUS.fix_quality

        if fix_quality == 1:
            if quality_one_started_at is None:
                return now
            if now - quality_one_started_at > GNSS_FIX_QUALITY_RECONNECT_SEC:
                raise NtripReconnectTrigger(
                    "gnss_quality_1_persisted",
                    "GNSS fix quality remained 1 for more than 60 seconds while RTCM stayed fresh; reconnecting NTRIP stream",
                )
            return quality_one_started_at
        return None

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
                consecutive_failures=consecutive_failures,
                locked_out=False,
                lockout_reason=None,
            )

            last_rtcm_data_at = time.time()
            last_gga_sent_at = 0.0
            has_sent_gga = False
            waiting_for_gga_logged = False
            stable_rtcm_started_at: float | None = None
            stable_reset_done = consecutive_failures == 0
            quality_one_started_at: float | None = None

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
                stable_rtcm_started_at = last_rtcm_data_at

            while not stop_event.is_set():
                now = time.time()
                quality_one_started_at = evaluate_reconnect_health(
                    now=now,
                    last_rtcm_data_at=last_rtcm_data_at,
                    quality_one_started_at=quality_one_started_at,
                )
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
                    if stable_rtcm_started_at is None:
                        stable_rtcm_started_at = last_rtcm_data_at
                    if (
                        not stable_reset_done
                        and stable_rtcm_started_at is not None
                        and (last_rtcm_data_at - stable_rtcm_started_at) >= STABLE_RTCM_RESET_SEC
                    ):
                        stable_reset_done = True
                        clear_failure_state(response_line)
                        logging.info(
                            "NTRIP stream stable for %s seconds; resetting consecutive failure counter",
                            STABLE_RTCM_RESET_SEC,
                        )
                    quality_one_started_at = evaluate_reconnect_health(
                        now=time.time(),
                        last_rtcm_data_at=last_rtcm_data_at,
                        quality_one_started_at=quality_one_started_at,
                    )
                except socket.timeout:
                    quality_one_started_at = evaluate_reconnect_health(
                        now=time.time(),
                        last_rtcm_data_at=last_rtcm_data_at,
                        quality_one_started_at=quality_one_started_at,
                    )
                    if gga_forward_enabled and not has_sent_gga:
                        if not waiting_for_gga_logged:
                            logging.info("Connected to NTRIP caster and waiting for first GGA before monitoring quality-based reconnect")
                            waiting_for_gga_logged = True
        except NtripPermanentError as exc:
            lockout_reason = f"{exc}. Use web reconnect to try again."
            record_failure(lockout_reason)
            if wait_for_manual_reconnect(lockout_reason):
                consecutive_failures = 0
                recovery_reason_pending = None
                set_correction_runtime_state(
                    mode="ntrip",
                    connected=False,
                    last_error="Reconnect requested from web viewer",
                    consecutive_failures=0,
                    locked_out=False,
                    lockout_reason=None,
                )
                logging.info("NTRIP reconnect requested from web viewer; resuming attempts")
        except NtripReconnectTrigger as exc:
            if recovery_reason_pending is not None:
                record_failure(f"{recovery_reason_pending}; reconnect cycle did not restore a stable RTCM stream")
            recovery_reason_pending = str(exc)
            set_correction_runtime_state(
                mode="ntrip",
                connected=False,
                last_error=recovery_reason_pending,
                last_response=None,
                consecutive_failures=consecutive_failures,
                locked_out=False,
            )
            logging.warning("NTRIP reconnect trigger (%s): %s", exc.reason_code, exc)
        except Exception as exc:
            record_failure(str(exc))
            logging.error("NTRIP error: %s", exc)
            if not stop_event.is_set():
                reconnect_delay = reconnect_delay_for_failure_count(consecutive_failures)
                logging.info("Reconnecting NTRIP in %s seconds...", reconnect_delay)
                time.sleep(reconnect_delay)
        finally:
            if sock is not None:
                try:
                    sock.close()
                except Exception:
                    pass

    set_correction_runtime_state(mode="ntrip", connected=False, locked_out=False)
    logging.info("NTRIP loop stopped")
