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
    consecutive_failures: int | None = None,
    locked_out: bool | None = None,
    lockout_reason: str | None = None,
) -> None:
    with STATUS_LOCK:
        STATUS.correction_source_mode = mode
        STATUS.ntrip_connected = connected
        STATUS.ntrip_last_error = last_error
        STATUS.ntrip_last_response = last_response
        if consecutive_failures is not None:
            STATUS.ntrip_consecutive_failures = consecutive_failures
        if locked_out is not None:
            STATUS.ntrip_locked_out = locked_out
        if lockout_reason is not None or locked_out is False:
            STATUS.ntrip_lockout_reason = lockout_reason


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
