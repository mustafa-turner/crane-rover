from __future__ import annotations

import logging
import time

import serial
from pynmeagps import NMEAReader

from rover.state import store_latest_gga, update_status_from_gga, update_status_from_gsa


def open_serial(serial_cfg: dict) -> serial.Serial:
    return serial.Serial(
        port=serial_cfg["port"],
        baudrate=serial_cfg.get("baudrate", 115200),
        timeout=1,
    )


def nmea_reader_loop(ser: serial.Serial, stop_event) -> None:
    logging.info("NMEA reader started on %s", ser.port)
    reader = NMEAReader(ser, validate=0)

    while not stop_event.is_set():
        try:
            raw_data, parsed_data = reader.read()
            if raw_data is None or parsed_data is None:
                continue

            msg_id = getattr(parsed_data, "msgID", "")
            if msg_id == "GGA":
                store_latest_gga(raw_data)
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
