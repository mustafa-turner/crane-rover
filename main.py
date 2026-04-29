import logging
import sys
import threading
import time

from rover.battery import battery_monitor_loop
from rover.blynk import blynk_loop
from rover.config import load_config, setup_logging
from rover.gnss import nmea_reader_loop, open_serial
from rover.ntrip import ntrip_loop
from rover.status import status_printer_loop


def main() -> int:
    config_path = sys.argv[1] if len(sys.argv) > 1 else "config.yaml"
    config = load_config(config_path)

    setup_logging(config.get("logging", {}).get("level", "INFO"))

    serial_cfg = config["serial"]
    status_cfg = config.get("status", {})
    blynk_cfg = config.get("blynk", {})
    battery_cfg = config.get("battery", {})

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
    battery_thread = None
    if battery_cfg.get("enabled", False):
        battery_thread = threading.Thread(
            target=battery_monitor_loop,
            args=(battery_cfg, stop_event),
            daemon=True,
        )

    blynk_thread = None
    if blynk_cfg.get("enabled", False):
        blynk_thread = threading.Thread(
            target=blynk_loop,
            args=(blynk_cfg, stop_event),
            daemon=True,
        )

    nmea_thread.start()
    ntrip_thread.start()
    printer_thread.start()
    if battery_thread is not None:
        battery_thread.start()
    if blynk_thread is not None:
        blynk_thread.start()

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
        if battery_thread is not None:
            battery_thread.join(timeout=2)
        if blynk_thread is not None:
            blynk_thread.join(timeout=2)
        ser.close()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
