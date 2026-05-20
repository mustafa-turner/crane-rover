from __future__ import annotations

import logging
import socket
import time
from typing import Optional

from rover.rtcm_common import process_rtcm_data, set_correction_runtime_state
from rover.state import RtcmStreamInspector


def tcp_rtcm_loop(ser, tcp_cfg: dict, rtcm_cfg: dict, stop_event) -> None:
    host = str(tcp_cfg.get("host", ""))
    port = int(tcp_cfg.get("port", 9000))
    connect_timeout = int(rtcm_cfg.get("connectTimeoutSec", 10))
    read_timeout = int(rtcm_cfg.get("readTimeoutSec", 15))
    reconnect_delay = int(rtcm_cfg.get("reconnectDelaySec", 5))
    chunk_size = int(rtcm_cfg.get("chunkSize", 1024))
    recv_poll_timeout = float(rtcm_cfg.get("recvPollTimeoutSec", 1.0))
    rtcm_log_interval = int(rtcm_cfg.get("rtcmLogIntervalSec", 10))

    if not host:
        raise ValueError("TCP RTCM mode requires tcp.host")

    logging.info("TCP RTCM loop started for %s:%s", host, port)
    inspector = RtcmStreamInspector()
    last_rtcm_log_at = 0.0

    while not stop_event.is_set():
        sock: Optional[socket.socket] = None
        try:
            logging.info("Connecting to TCP RTCM source %s:%s", host, port)
            sock = socket.create_connection((host, port), timeout=connect_timeout)
            sock.settimeout(recv_poll_timeout)
            set_correction_runtime_state(
                mode="tcp",
                connected=True,
                last_error=None,
                last_response=f"TCP {host}:{port}",
            )

            last_rtcm_data_at = time.time()
            while not stop_event.is_set():
                try:
                    data = sock.recv(chunk_size)
                    logging.debug("Received %d RTCM bytes from TCP source", len(data))
                    if not data:
                        raise ConnectionError("TCP RTCM connection closed by server")

                    last_rtcm_log_at = process_rtcm_data(
                        ser=ser,
                        data=data,
                        inspector=inspector,
                        rtcm_log_interval=rtcm_log_interval,
                        last_rtcm_log_at=last_rtcm_log_at,
                    )
                    last_rtcm_data_at = time.time()
                except socket.timeout:
                    if time.time() - last_rtcm_data_at >= read_timeout:
                        raise TimeoutError(f"No RTCM data received for {read_timeout} seconds from TCP source")
        except Exception as exc:
            set_correction_runtime_state(mode="tcp", connected=False, last_error=str(exc))
            logging.error("TCP RTCM error: %s", exc)
            if not stop_event.is_set():
                logging.info("Reconnecting TCP RTCM in %s seconds...", reconnect_delay)
                time.sleep(reconnect_delay)
        finally:
            if sock is not None:
                try:
                    sock.close()
                except Exception:
                    pass

    set_correction_runtime_state(mode="tcp", connected=False)
    logging.info("TCP RTCM loop stopped")
