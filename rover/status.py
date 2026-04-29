from __future__ import annotations

import time

from rover.state import STATUS, STATUS_LOCK, fmt, fmt_int, fmt_percent


def status_printer_loop(interval_sec: int, stop_event) -> None:
    while not stop_event.is_set():
        with STATUS_LOCK:
            lat = STATUS.latitude
            lon = STATUS.longitude
            alt = STATUS.altitude_m
            sats = STATUS.satellites
            hdop = STATUS.hdop
            fix_label = STATUS.fix_label
            ntrip_connected = STATUS.ntrip_connected
            ntrip_last_error = STATUS.ntrip_last_error
            last_rtcm_received_at = STATUS.last_rtcm_received_at
            last_nmea_at = STATUS.last_nmea_at
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

        print("\n=== ROVER STATUS ===", flush=True)
        print(f"Latitude        : {fmt(lat, 8)}", flush=True)
        print(f"Longitude       : {fmt(lon, 8)}", flush=True)
        print(f"Altitude (m)    : {fmt(alt, 3)}", flush=True)
        print(f"Satellites      : {fmt_int(sats)}", flush=True)
        print(f"HDOP            : {fmt(hdop, 2)}", flush=True)
        print(f"Fix / RTK Mode  : {fix_label}", flush=True)
        print(f"Local Acc (m)   : {fmt(local_horizontal_accuracy_m, 3)}", flush=True)
        print(f"NTRIP Status    : {ntrip_text}", flush=True)
        print(f"Last RTCM Age   : {rtcm_age}", flush=True)
        print(f"RTCM Bytes      : {rtcm_bytes}", flush=True)
        print(f"RTCM Frames     : {rtcm_frames}", flush=True)
        print(f"RTCM Last Type  : {rtcm_last_type if rtcm_last_type is not None else '-'}", flush=True)
        print(f"RTCM Has ARP    : {'YES' if rtcm_has_station_frame else 'NO'}", flush=True)
        print(f"RTCM Has MSM    : {'YES' if rtcm_has_observation_frame else 'NO'}", flush=True)
        print(f"RTCM Recent     : {rtcm_recent_types}", flush=True)
        print(f"Last NMEA Age   : {nmea_age}", flush=True)
        print(f"Battery Level   : {fmt_percent(battery_percent)}", flush=True)
        print(f"Battery Voltage : {fmt(battery_voltage_v, 3)} V", flush=True)
        print(f"Battery Current : {fmt(battery_current_a, 3)} A", flush=True)
        print(f"Battery Power   : {fmt(battery_power_w, 3)} W", flush=True)
        print(f"Battery Status  : {battery_status}", flush=True)
        print(f"Battery Present : {battery_present_text}", flush=True)
        print(f"Battery Age     : {battery_age}", flush=True)
        print(f"Peer Tx Age     : {peer_broadcast_age}", flush=True)
        print(f"Peer Rx Age     : {peer_receive_age}", flush=True)
        print(f"Peer Count      : {len(peers)}", flush=True)
        if ntrip_last_error:
            print(f"NTRIP Error     : {ntrip_last_error}", flush=True)
        if battery_last_error:
            print(f"Battery Error   : {battery_last_error}", flush=True)
        if peer_last_error:
            print(f"Peer UDP Error  : {peer_last_error}", flush=True)
        if peers:
            print("--- PEERS ---", flush=True)
            for peer in sorted(peers, key=lambda item: item.device_id):
                peer_age_sec = None if peer.received_at is None else (now - peer.received_at)
                is_stale = (
                    peer_age_sec is None
                    or (peer.max_message_age_sec > 0 and peer_age_sec > peer.max_message_age_sec)
                )
                distance_text = fmt(peer.distance_m, 3) if not is_stale else "-"
                combined_accuracy_text = fmt(peer.combined_accuracy_m, 3) if not is_stale else "-"
                peer_age_text = "-" if peer_age_sec is None else f"{peer_age_sec:.1f}s"
                freshness = "STALE" if is_stale else "FRESH"
                print(
                    f"{peer.device_id:<15} dist={distance_text} m age={peer_age_text} "
                    f"fix={peer.fix_label} acc={fmt(peer.accuracy_m, 3)} m "
                    f"combined={combined_accuracy_text} m {freshness}",
                    flush=True,
                )
        print("====================\n", flush=True)

        stop_event.wait(interval_sec)
