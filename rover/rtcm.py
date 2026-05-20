from __future__ import annotations

import logging

from rover.ntrip import ntrip_loop
from rover.rtcm_common import set_correction_runtime_state
from rover.tcp import tcp_rtcm_loop


def correction_loop(ser, rtcm_cfg: dict, ntrip_cfg: dict, tcp_cfg: dict, stop_event) -> None:
    mode = str(rtcm_cfg.get("mode", "ntrip")).strip().lower()
    try:
        if mode == "tcp":
            tcp_rtcm_loop(ser, tcp_cfg, rtcm_cfg, stop_event)
            return
        if mode == "ntrip":
            ntrip_loop(ser, ntrip_cfg, rtcm_cfg, stop_event)
            return
        raise ValueError(f"Unsupported RTCM correction mode: {mode}")
    except Exception as exc:
        set_correction_runtime_state(mode=mode, connected=False, last_error=str(exc))
        logging.error("Correction loop stopped: %s", exc)
