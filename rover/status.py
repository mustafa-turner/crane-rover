from __future__ import annotations

import logging
import time

from rover.state import STATUS, STATUS_LOCK, fmt, fmt_int, fmt_percent


DEFAULT_STATUS_INTERVAL_SEC = 0.5


def status_printer_loop(interval_sec: float, stop_event, console=None, mode: str = "normal") -> None:
    while not stop_event.is_set():
        if console is not None and console.should_pause_live_output():
            stop_event.wait(0.2)
            continue

        with STATUS_LOCK:
            lat = STATUS.latitude
            lon = STATUS.longitude
            alt = STATUS.altitude_m
            sats = STATUS.satellites
            hdop = STATUS.hdop
            fix_label = STATUS.fix_label
            ntrip_connected = STATUS.ntrip_connected
            ntrip_last_error = STATUS.ntrip_last_error
            ntrip_last_response = STATUS.ntrip_last_response
            last_rtcm_received_at = STATUS.last_rtcm_received_at
            last_nmea_at = STATUS.last_nmea_at
            last_gga_at = STATUS.last_gga_at
            last_gga_sent_at = STATUS.last_gga_sent_at
            rtcm_bytes = STATUS.rtcm_bytes
            rtcm_frames = STATUS.rtcm_frames
            rtcm_last_type = STATUS.rtcm_last_type
            rtcm_has_station_frame = STATUS.rtcm_has_station_frame
            rtcm_has_observation_frame = STATUS.rtcm_has_observation_frame
            rtcm_recent_types = STATUS.rtcm_recent_types
            battery_percent = STATUS.battery_percent
            battery_voltage_v = STATUS.battery_voltage_v
            battery_current_a = STATUS.battery_current_a
            battery_power_w = STATUS.battery_power_w
            battery_status = STATUS.battery_status
            battery_present = STATUS.battery_present
            battery_last_update_at = STATUS.battery_last_update_at
            battery_last_error = STATUS.battery_last_error
            local_horizontal_accuracy_m = STATUS.local_horizontal_accuracy_m
            peer_last_broadcast_at = STATUS.peer_last_broadcast_at
            peer_last_receive_at = STATUS.peer_last_receive_at
            peer_last_error = STATUS.peer_last_error
            peers = list(STATUS.peers.values())

        now = time.time()
        rtcm_age = "-"
        if last_rtcm_received_at is not None:
            rtcm_age = f"{now - last_rtcm_received_at:.1f}s"

        nmea_age = "-"
        if last_nmea_at is not None:
            nmea_age = f"{now - last_nmea_at:.1f}s"

        gga_age = "-"
        if last_gga_at is not None:
            gga_age = f"{now - last_gga_at:.1f}s"

        gga_sent_age = "-"
        if last_gga_sent_at is not None:
            gga_sent_age = f"{now - last_gga_sent_at:.1f}s"

        battery_age = "-"
        if battery_last_update_at is not None:
            battery_age = f"{now - battery_last_update_at:.1f}s"

        peer_broadcast_age = "-"
        if peer_last_broadcast_at is not None:
            peer_broadcast_age = f"{now - peer_last_broadcast_at:.1f}s"

        peer_receive_age = "-"
        if peer_last_receive_at is not None:
            peer_receive_age = f"{now - peer_last_receive_at:.1f}s"

        ntrip_text = "CONNECTED" if ntrip_connected else "DISCONNECTED"
        battery_present_text = "-"
        if battery_present is True:
            battery_present_text = "YES"
        elif battery_present is False:
            battery_present_text = "NO"
        status_time = time.strftime("%Y-%m-%d %H:%M:%S")

        status_mode = (mode or "normal").strip().lower()
        if status_mode == "debug":
            lines = [
                "",
                "=== ROVER STATUS (DEBUG) ===",
                f"Status Time     : {status_time}",
                f"Latitude        : {fmt(lat, 8)}",
                f"Longitude       : {fmt(lon, 8)}",
                f"Altitude (m)    : {fmt(alt, 3)}",
                f"Satellites      : {fmt_int(sats)}",
                f"HDOP            : {fmt(hdop, 2)}",
                f"Fix / RTK Mode  : {fix_label}",
                f"Local Acc (m)   : {fmt(local_horizontal_accuracy_m, 3)}",
                f"NTRIP Status    : {ntrip_text}",
                f"NTRIP Response  : {ntrip_last_response or '-'}",
                f"Last RTCM Age   : {rtcm_age}",
                f"RTCM Bytes      : {rtcm_bytes}",
                f"RTCM Frames     : {rtcm_frames}",
                f"RTCM Last Type  : {rtcm_last_type if rtcm_last_type is not None else '-'}",
                f"RTCM Has ARP    : {'YES' if rtcm_has_station_frame else 'NO'}",
                f"RTCM Has MSM    : {'YES' if rtcm_has_observation_frame else 'NO'}",
                f"RTCM Recent     : {rtcm_recent_types}",
                f"Last NMEA Age   : {nmea_age}",
                f"Last GGA Age    : {gga_age}",
                f"Last GGA Sent   : {gga_sent_age}",
                f"Battery Level   : {fmt_percent(battery_percent)}",
                f"Battery Voltage : {fmt(battery_voltage_v, 3)} V",
                f"Battery Current : {fmt(battery_current_a, 3)} A",
                f"Battery Power   : {fmt(battery_power_w, 3)} W",
                f"Battery Status  : {battery_status}",
                f"Battery Present : {battery_present_text}",
                f"Battery Age     : {battery_age}",
                f"Peer Tx Age     : {peer_broadcast_age}",
                f"Peer Rx Age     : {peer_receive_age}",
                f"Peer Count      : {len(peers)}",
            ]
        else:
            lines = [
                "",
                "=== ROVER STATUS ===",
                f"Status Time     : {status_time}",
                f"Latitude        : {fmt(lat, 8)}",
                f"Longitude       : {fmt(lon, 8)}",
                f"Altitude (m)    : {fmt(alt, 3)}",
                f"Satellites      : {fmt_int(sats)}",
                f"HDOP            : {fmt(hdop, 2)}",
                f"Fix / RTK Mode  : {fix_label}",
                f"Local Acc (m)   : {fmt(local_horizontal_accuracy_m, 3)}",
                f"NTRIP Status    : {ntrip_text}",
                f"Last RTCM Age   : {rtcm_age}",
                f"Last NMEA Age   : {nmea_age}",
                f"Last GGA Age    : {gga_age}",
                f"Battery Level   : {fmt_percent(battery_percent)}",
                f"Battery Voltage : {fmt(battery_voltage_v, 3)} V",
                f"Peer Count      : {len(peers)}",
            ]
            if battery_current_a is not None:
                lines.append(f"Battery Current : {fmt(battery_current_a, 3)} A")
            if battery_power_w is not None:
                lines.append(f"Battery Power   : {fmt(battery_power_w, 3)} W")
        if ntrip_last_error:
            lines.append(f"NTRIP Error     : {ntrip_last_error}")
        if battery_last_error:
            lines.append(f"Battery Error   : {battery_last_error}")
        if peer_last_error:
            lines.append(f"Peer UDP Error  : {peer_last_error}")
        if peers:
            lines.append("--- PEERS ---")
            for peer in sorted(peers, key=lambda item: item.device_id):
                peer_age_sec = None if peer.received_at is None else (now - peer.received_at)
                is_stale = (
                    peer_age_sec is None
                    or (peer.max_message_age_sec > 0 and peer_age_sec > peer.max_message_age_sec)
                )
                distance_text = fmt(peer.distance_m, 3) if not is_stale else "-"
                conservative_distance_text = fmt(peer.conservative_distance_m, 3) if not is_stale else "-"
                uncertainty_text = fmt(peer.combined_accuracy_m, 3) if not is_stale else "-"
                peer_age_text = "-" if peer_age_sec is None else f"{peer_age_sec:.1f}s"
                freshness = "STALE" if is_stale else "FRESH"
                if status_mode == "debug":
                    lines.append(
                        f"{peer.device_id:<15} safe={conservative_distance_text} m raw={distance_text} m age={peer_age_text} "
                        f"fix={peer.fix_label} acc={fmt(peer.accuracy_m, 3)} m "
                        f"uncertainty={uncertainty_text} m {freshness}"
                    )
                else:
                    lines.append(
                        f"{peer.device_id:<15} safe={conservative_distance_text} m raw={distance_text} m "
                        f"age={peer_age_text} fix={peer.fix_label} {freshness}"
                    )
        lines.extend(["====================", ""])

        try:
            if console is not None:
                console.write_status_block(lines)
            else:
                print("\n".join(lines), flush=True)
        except Exception as exc:
            logging.error("Status printer write error: %s", exc)

        stop_event.wait(interval_sec)
