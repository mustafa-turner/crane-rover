from __future__ import annotations

import logging
import time

from rover.state import STATUS, STATUS_LOCK, update_status_from_rtcm


def set_correction_runtime_state(
    *,
    mode: str,
    connected: bool,
    last_error: str | None = None,
    last_response: str | None = None,
) -> None:
    with STATUS_LOCK:
        STATUS.correction_source_mode = mode
        STATUS.ntrip_connected = connected
        STATUS.ntrip_last_error = last_error
        STATUS.ntrip_last_response = last_response


def process_rtcm_data(
    *,
    ser,
    data: bytes,
    inspector,
    rtcm_log_interval: int,
    last_rtcm_log_at: float,
) -> float:
    ser.write(data)
    ser.flush()
    rtcm_types = inspector.feed(data)

    now = time.time()
    with STATUS_LOCK:
        STATUS.last_rtcm_received_at = now

    update_status_from_rtcm(rtcm_types, len(data), inspector)
    if rtcm_types and (now - last_rtcm_log_at >= rtcm_log_interval):
        logging.info("Recent RTCM types: %s", inspector.describe_recent())
        return now
    return last_rtcm_log_at
