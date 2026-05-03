import logging
import os
import sys
import threading
import time

from rover.battery import battery_monitor_loop
from rover.blynk import blynk_loop
from rover.config import load_config, setup_logging
from rover.console import SerialConsoleManager
from rover.gnss import nmea_reader_loop, open_serial
from rover.ntrip import ntrip_loop
from rover.peer_udp import peer_udp_loop
from rover.status import status_printer_loop
from rover.wifi import apply_preferred_wifi, wifi_monitor_loop


def main() -> int:
    config_path = sys.argv[1] if len(sys.argv) > 1 else "config.yaml"
    config = load_config(config_path)

    console = SerialConsoleManager(config_path, config)
    setup_logging(
        config.get("logging", {}).get("level", "INFO"),
        handlers=console.create_log_handlers(),
    )

    serial_cfg = config["serial"]
    status_cfg = config.get("status", {})
    blynk_cfg = config.get("blynk", {})
    battery_cfg = config.get("battery", {})
    peer_cfg = config.get("peerUdp", {})
    wifi_cfg = config.get("wifi", {})

    apply_preferred_wifi(wifi_cfg)

    ser = open_serial(serial_cfg)
    stop_event = threading.Event()
    restart_requested = False

    console.start()

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
    printer_thread = None
    if status_cfg.get("enabled", True):
        printer_thread = threading.Thread(
            target=status_printer_loop,
            args=(int(status_cfg.get("printIntervalSec", 2)), stop_event, console),
            daemon=True,
        )
    battery_thread = None
    if battery_cfg.get("enabled", False):
        battery_thread = threading.Thread(
            target=battery_monitor_loop,
            args=(battery_cfg, stop_event),
            daemon=True,
        )
    peer_thread = None
    if peer_cfg.get("enabled", False):
        peer_thread = threading.Thread(
            target=peer_udp_loop,
            args=(peer_cfg, stop_event),
            daemon=True,
        )

    blynk_thread = None
    if blynk_cfg.get("enabled", False):
        blynk_thread = threading.Thread(
            target=blynk_loop,
            args=(blynk_cfg, stop_event),
            daemon=True,
        )
    wifi_thread = None
    if wifi_cfg.get("enabled", False):
        wifi_thread = threading.Thread(
            target=wifi_monitor_loop,
            args=(wifi_cfg, stop_event),
            daemon=True,
        )

    nmea_thread.start()
    ntrip_thread.start()
    if printer_thread is not None:
        printer_thread.start()
    if battery_thread is not None:
        battery_thread.start()
    if peer_thread is not None:
        peer_thread.start()
    if blynk_thread is not None:
        blynk_thread.start()
    if wifi_thread is not None:
        wifi_thread.start()

    logging.info("Stage 1 rover started.")

    try:
        while True:
            if console.consume_menu_request():
                menu_result = console.run_menu(config)
                if menu_result.restart_requested:
                    restart_requested = True
                    break
            time.sleep(0.2)
    except KeyboardInterrupt:
        logging.info("Stopping...")
    finally:
        stop_event.set()
        console.stop()
        nmea_thread.join(timeout=2)
        ntrip_thread.join(timeout=2)
        if printer_thread is not None:
            printer_thread.join(timeout=2)
        if battery_thread is not None:
            battery_thread.join(timeout=2)
        if peer_thread is not None:
            peer_thread.join(timeout=2)
        if blynk_thread is not None:
            blynk_thread.join(timeout=2)
        if wifi_thread is not None:
            wifi_thread.join(timeout=2)
        ser.close()

    if restart_requested:
        logging.shutdown()
        os.execv(sys.executable, [sys.executable, *sys.argv])

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
