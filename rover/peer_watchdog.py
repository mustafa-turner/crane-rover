from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass

from rover.state import STATUS, STATUS_LOCK


PEER_WATCHDOG_RESTART_EXIT_CODE = 75
DEFAULT_PEER_WATCHDOG_CHECK_INTERVAL_SEC = 5.0
DEFAULT_PEER_WATCHDOG_STARTUP_GRACE_SEC = 60.0


@dataclass(frozen=True)
class PeerDistanceHealth:
    ok: bool
    reason: str
    peer_count: int
    fresh_peer_count: int
    distance_peer_count: int
    last_receive_age_sec: float | None


def get_peer_distance_health(now: float | None = None) -> PeerDistanceHealth:
    now = time.time() if now is None else now
    with STATUS_LOCK:
        peers = list(STATUS.peers.values())
        peer_last_receive_at = STATUS.peer_last_receive_at

    fresh_peers = []
    peers_with_distance = []
    newest_receive_at = peer_last_receive_at
    for peer in peers:
        if peer.received_at is None:
            continue
        if newest_receive_at is None or peer.received_at > newest_receive_at:
            newest_receive_at = peer.received_at

        peer_age_sec = max(0.0, now - peer.received_at)
        if peer.max_message_age_sec > 0 and peer_age_sec > peer.max_message_age_sec:
            continue
        fresh_peers.append(peer)
        if peer.distance_m is not None:
            peers_with_distance.append(peer)

    last_receive_age_sec = None
    if newest_receive_at is not None:
        last_receive_age_sec = max(0.0, now - newest_receive_at)

    if peers_with_distance:
        return PeerDistanceHealth(
            ok=True,
            reason=f"{len(peers_with_distance)} fresh peer distance(s) available",
            peer_count=len(peers),
            fresh_peer_count=len(fresh_peers),
            distance_peer_count=len(peers_with_distance),
            last_receive_age_sec=last_receive_age_sec,
        )

    if fresh_peers:
        reason = (
            f"{len(fresh_peers)} fresh peer message(s), but no usable distance; "
            "local or peer coordinates are missing"
        )
    elif peers:
        if last_receive_age_sec is None:
            reason = f"{len(peers)} known peer(s), but none have receive timestamps"
        else:
            reason = f"{len(peers)} known peer(s), none fresh; last peer received {last_receive_age_sec:.1f}s ago"
    else:
        reason = "no peer messages received yet"

    return PeerDistanceHealth(
        ok=False,
        reason=reason,
        peer_count=len(peers),
        fresh_peer_count=len(fresh_peers),
        distance_peer_count=0,
        last_receive_age_sec=last_receive_age_sec,
    )


def peer_distance_watchdog_loop(
    peer_cfg: dict,
    restart_requested: threading.Event,
    stop_event: threading.Event,
) -> None:
    restart_after_sec = _float_setting(peer_cfg, "autoRestartOnMissingPeerDistanceSec", 0.0)
    if restart_after_sec <= 0:
        return

    check_interval_sec = max(
        0.5,
        _float_setting(
            peer_cfg,
            "autoRestartCheckIntervalSec",
            DEFAULT_PEER_WATCHDOG_CHECK_INTERVAL_SEC,
        ),
    )
    startup_grace_sec = max(
        0.0,
        _float_setting(
            peer_cfg,
            "autoRestartStartupGraceSec",
            DEFAULT_PEER_WATCHDOG_STARTUP_GRACE_SEC,
        ),
    )

    started_at = time.time()
    unhealthy_started_at: float | None = None
    last_warning_at = 0.0

    logging.info(
        "Peer distance watchdog enabled: restart after %.1fs without a usable peer distance "
        "(startup grace %.1fs, check interval %.1fs)",
        restart_after_sec,
        startup_grace_sec,
        check_interval_sec,
    )

    while not stop_event.wait(check_interval_sec):
        now = time.time()
        health = get_peer_distance_health(now)
        if health.ok:
            if unhealthy_started_at is not None:
                logging.info("Peer distance watchdog recovered: %s", health.reason)
            unhealthy_started_at = None
            continue

        if now - started_at < startup_grace_sec:
            continue

        if unhealthy_started_at is None:
            unhealthy_started_at = now
            last_warning_at = now
            logging.warning("Peer distance watchdog detected missing distance: %s", health.reason)
            continue

        unhealthy_for_sec = now - unhealthy_started_at
        if now - last_warning_at >= 30.0:
            last_warning_at = now
            logging.warning(
                "Peer distance watchdog still missing distance for %.1fs/%.1fs: %s",
                unhealthy_for_sec,
                restart_after_sec,
                health.reason,
            )

        if unhealthy_for_sec >= restart_after_sec:
            logging.error(
                "Peer distance watchdog requesting service restart after %.1fs without a usable peer distance: %s",
                unhealthy_for_sec,
                health.reason,
            )
            restart_requested.set()
            return


def _float_setting(config: dict, key: str, default: float) -> float:
    try:
        return float(config.get(key, default))
    except (TypeError, ValueError):
        logging.warning("Invalid peerUdp.%s value %r; using %.1f", key, config.get(key), default)
        return default
