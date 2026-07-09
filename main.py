import logging
import os
import socket
import sys
import threading
import time

from rover.battery import battery_monitor_loop
from rover.blynk import blynk_loop, mqtt_loop
from rover.config import load_config, setup_logging
from rover.console import SerialConsoleManager
from rover.gnss import nmea_reader_loop, open_serial
from rover.peer_udp import peer_udp_loop
from rover.peer_watchdog import PEER_WATCHDOG_RESTART_EXIT_CODE, peer_distance_watchdog_loop
from rover.rtcm import correction_loop
from rover.status import DEFAULT_STATUS_INTERVAL_SEC, status_printer_loop
from rover.web import web_viewer_loop
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
    mqtt_cfg = config.get("mqtt", {})
    battery_cfg = config.get("battery", {})
    peer_cfg = config.get("peerUdp", {})
    wifi_cfg = config.get("wifi", {})
    web_cfg = config.get("web", {})
    rtcm_cfg = config.get("rtcm", {})
    ntrip_cfg = config.get("ntrip", {})
    tcp_cfg = config.get("tcp", {})
    rover_name = str(peer_cfg.get("deviceId") or socket.gethostname())

    apply_preferred_wifi(wifi_cfg)

    ser = open_serial(serial_cfg)
    stop_event = threading.Event()
    peer_watchdog_restart_requested = threading.Event()
    restart_requested = False

    console.start()

    nmea_thread = threading.Thread(
        target=nmea_reader_loop,
        args=(ser, stop_event),
        daemon=True,
    )
    correction_thread = threading.Thread(
        target=correction_loop,
        args=(ser, rtcm_cfg, ntrip_cfg, tcp_cfg, stop_event),
        daemon=True,
    )
    printer_thread = None
    if status_cfg.get("enabled", True):
        status_interval_sec = float(status_cfg.get("intervalSec", DEFAULT_STATUS_INTERVAL_SEC))
        printer_thread = threading.Thread(
            target=status_printer_loop,
            args=(status_interval_sec, stop_event, console, status_cfg.get("mode", "normal")),
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
    peer_watchdog_thread = None
    if peer_cfg.get("enabled", False):
        peer_watchdog_thread = threading.Thread(
            target=peer_distance_watchdog_loop,
            args=(peer_cfg, peer_watchdog_restart_requested, stop_event),
            daemon=True,
        )

    blynk_thread = None
    if blynk_cfg.get("enabled", False):
        blynk_runtime_cfg = dict(blynk_cfg)
        blynk_runtime_cfg["roverName"] = rover_name
        blynk_thread = threading.Thread(
            target=blynk_loop,
            args=(blynk_runtime_cfg, stop_event),
            daemon=True,
        )
    wifi_thread = None
    if wifi_cfg.get("enabled", False):
        wifi_thread = threading.Thread(
            target=wifi_monitor_loop,
            args=(wifi_cfg, stop_event),
            daemon=True,
        )
    mqtt_thread = None
    if mqtt_cfg.get("enabled", False):
        mqtt_runtime_cfg = dict(mqtt_cfg)
        mqtt_runtime_cfg["roverName"] = rover_name
        mqtt_thread = threading.Thread(
            target=mqtt_loop,
            args=(mqtt_runtime_cfg, stop_event),
            daemon=True,
        )
    web_thread = None
    if web_cfg.get("enabled", False):
        web_thread = threading.Thread(
            target=web_viewer_loop,
            args=(web_cfg, rover_name, stop_event),
            daemon=True,
        )

    nmea_thread.start()
    correction_thread.start()
    if printer_thread is not None:
        printer_thread.start()
    if battery_thread is not None:
        battery_thread.start()
    if peer_thread is not None:
        peer_thread.start()
    if peer_watchdog_thread is not None:
        peer_watchdog_thread.start()
    if blynk_thread is not None:
        blynk_thread.start()
    if wifi_thread is not None:
        wifi_thread.start()
    if mqtt_thread is not None:
        mqtt_thread.start()
    if web_thread is not None:
        web_thread.start()

    logging.info("Stage 1 rover started.")

    try:
        while True:
            if peer_watchdog_restart_requested.is_set():
                logging.error("Restarting because the peer distance watchdog requested it")
                break
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
        correction_thread.join(timeout=2)
        if printer_thread is not None:
            printer_thread.join(timeout=2)
        if battery_thread is not None:
            battery_thread.join(timeout=2)
        if peer_thread is not None:
            peer_thread.join(timeout=2)
        if peer_watchdog_thread is not None:
            peer_watchdog_thread.join(timeout=2)
        if blynk_thread is not None:
            blynk_thread.join(timeout=2)
        if wifi_thread is not None:
            wifi_thread.join(timeout=2)
        if mqtt_thread is not None:
            mqtt_thread.join(timeout=2)
        if web_thread is not None:
            web_thread.join(timeout=2)
        ser.close()

    if restart_requested:
        logging.shutdown()
        os.execv(sys.executable, [sys.executable, *sys.argv])

    if peer_watchdog_restart_requested.is_set():
        logging.shutdown()
        return PEER_WATCHDOG_RESTART_EXIT_CODE

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
