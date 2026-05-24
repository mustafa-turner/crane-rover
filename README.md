# Crane Rover

Python rover application for Raspberry Pi based crane positioning nodes.

This project is built around a GNSS receiver, an RTCM correction link, an optional Waveshare UPS HAT (C), UDP peer broadcasting between rovers, and MQTT publishing to Blynk plus an optional second broker.

## What It Does

Each rover can:

- read NMEA from a GNSS receiver over serial
- select a preferred Wi-Fi network from up to four configured SSIDs
- connect to either an NTRIP caster or a plain TCP RTCM stream and forward corrections back into the receiver
- track rover fix mode, HDOP, RTCM activity, and battery telemetry
- broadcast its latest timestamped position and accuracy to nearby rover devices over UDP
- compute distance to the nearest peer rover
- compute a conservative safety distance by subtracting uncertainty from raw distance
- optionally expose a lightweight safety web viewer for operators
- publish local rover telemetry and nearest-peer safety data to Blynk
- optionally publish the same telemetry to a second MQTT broker at the same time
- print a live operator status view in the terminal
- expose a simple keyboard-driven settings menu when running on an attached terminal

## High-Level Architecture

`main.py` starts a set of worker threads:

- `rover/gnss.py`
  Reads NMEA sentences from the GNSS receiver.
- `rover/wifi.py`
  Selects the first available configured Wi-Fi network through NetworkManager.
- `rover/rtcm.py`
  Selects the configured RTCM transport.
- `rover/ntrip.py`
  Connects to an NTRIP caster and forwards correction data to the receiver.
- `rover/tcp.py`
  Connects to a plain TCP RTCM stream and forwards correction data to the receiver.
- `rover/status.py`
  Prints current rover state to the terminal.
- `rover/battery.py`
  Reads battery telemetry, usually from a Waveshare UPS HAT (C) INA219 over I2C.
- `rover/peer_udp.py`
  Broadcasts rover state to peers and listens for peer messages.
- `rover/blynk.py`
  Publishes rover telemetry to Blynk and an optional second MQTT broker.
- `rover/web.py`
  Serves a lightweight operator viewer for nearest-peer safety state.
- `rover/state.py`
  Shared in-memory state used by all threads.
- `rover/config.py`
  YAML config loading and logging setup.
- `rover/console.py`
  Optional terminal menu for editing `config.yaml` and restarting the process.

The design is intentionally simple:

- `main.py` only wires modules together
- each module has one main responsibility
- all threads communicate through shared state in `rover/state.py`

## Runtime Flow

At runtime, the normal flow is:

1. Optionally switch Wi-Fi to the first available configured SSID.
2. Open the GNSS serial port.
3. Start reading NMEA from the receiver.
4. Start the configured RTCM source and inject corrections into the receiver.
5. Update shared rover state from GNSS and RTCM data.
6. Optionally read UPS battery telemetry.
7. Broadcast local rover state to peers over UDP.
8. Receive peer state and calculate nearest-peer distance and safety margin.
9. Print everything to the terminal.
10. Optionally serve a simple operator web page for safety glanceability.
11. Optionally publish a compact telemetry payload to Blynk and an optional second MQTT broker.
12. If running on a real TTY, enter the settings menu when any key is pressed.

## Project Files

- `main.py`
- `rover/config.py`
- `rover/state.py`
- `rover/gnss.py`
- `rover/rtcm.py`
- `rover/ntrip.py`
- `rover/tcp.py`
- `rover/battery.py`
- `rover/peer_udp.py`
- `rover/blynk.py`
- `rover/status.py`
- `rover/web.py`
- `requirements.txt`
- `config.example.yaml`
- `config.yaml`
  Local-only file, not committed.

## Requirements

Typical environment:

- Raspberry Pi
- Python 3
- GNSS receiver connected by UART/serial
- NetworkManager with `nmcli` available if Wi-Fi auto-selection is enabled
- NTRIP caster credentials or a reachable TCP RTCM source on the same network
- network access for RTCM correction delivery and optionally MQTT brokers such as Blynk
- optional Waveshare UPS HAT (C)
- optional multiple rovers on the same network for UDP peer ranging

## Installation

### 1. Create a virtual environment

```bash
python3 -m venv .venv
source .venv/bin/activate
```

### 2. Install Python dependencies

```bash
pip install --upgrade pip
pip install -r requirements.txt
```

### 3. If using I2C battery monitoring, enable I2C on the Pi

```bash
sudo raspi-config
```

Enable I2C under the interface settings, then reboot if needed.

### 4. If needed, install Pi I2C tooling

```bash
sudo apt-get update
sudo apt-get install -y i2c-tools python3-smbus
```

`smbus2` is already listed in `requirements.txt`, but `python3-smbus` and `i2c-tools` are still useful on Raspberry Pi OS for debugging.

### 5. Optional: Install ZeroTier

If the rover should be reachable over a ZeroTier network, install and join it on the Pi:

```bash
sudo apt update
sudo apt upgrade -y
curl -s https://install.zerotier.com | sudo bash
sudo zerotier-cli join <your_network_id>
sudo zerotier-cli status
```

After joining, authorize the device in ZeroTier Central under the target network's member list.

## USB Gadget Serial Setup

`raspi-config` does not configure USB gadget serial. It can configure the onboard UART, but not a USB CDC-ACM gadget device such as `/dev/ttyGS0`.

This project uses `/dev/ttyGS0` as the service-side console endpoint, hardcoded in [rover/console.py](/Users/mustafa/Documents/GitHub/crane-rover/rover/console.py:1).

### Applicability

Use this only on Raspberry Pi models and ports that support USB device mode.

Typical cases:

- Pi Zero / Zero 2 W: use the USB data port, not the power-only port
- Pi 4 / Pi 5 / Pi 500: use the board USB-C port if device mode is supported in the deployed OS/kernel configuration

### 1. Enable `dwc2` at boot

Add this line to `/boot/firmware/config.txt`:

```ini
dtoverlay=dwc2
```

Add `modules-load=dwc2,g_serial` to the kernel command line in `/boot/firmware/cmdline.txt`.

### 2. Reboot and verify

```bash
sudo reboot
```

After reboot, verify on the Pi:

```bash
ls -l /dev/ttyGS0
```

If gadget mode initialized correctly, `/dev/ttyGS0` should exist.

### 3. Connect From the Host

Connect the Raspberry Pi to the host using the correct USB device-mode port, then open the serial device exposed by the host operating system. Use `115200` baud.

On most hosts, the USB gadget will appear as a new serial device after enumeration. Use that device in your serial terminal application.

### Notes

- The rover app uses `/dev/ttyGS0` for the settings console and `serial.port` for the GNSS receiver. These must be different interfaces.
- If the host does not detect a new serial device, the Raspberry Pi is not enumerating as a USB serial gadget.
- If `/dev/ttyGS0` does not appear on the Pi, the hardware, selected USB port, cable, or OS configuration does not currently support gadget mode.
- Ensure the `dtoverlay=dwc2,dr_mode=peripheral` line applies to the active board section in `config.txt`.

## Configuration

Create the local config:

```bash
cp config.example.yaml config.yaml
```

If the rover will run as the `pi` service user, make sure that user can rewrite the config file from the serial menu:

```bash
sudo chown pi:pi /home/pi/crane-rover /home/pi/crane-rover/config.yaml
```

Then edit:

```bash
nano config.yaml
```

If the rover runs in an interactive terminal, the app supports live config editing:

- leave it idle to watch logs and status
- press any key to open the settings menu
- enter section numbers such as `1` for `serial`, `2` for `wifi`, `3` for `rtcm`, `4` for `ntrip`, or `5` for `tcp`
- drill down into fields, enter a new value, then use `s` to save and restart
- use `q` to leave the menu without saving

The editor infers the value type from the current setting:

- booleans accept `true/false`, `yes/no`, `on/off`, or `1/0`
- integers accept decimal or hex such as `67` or `0x43`
- floating-point values accept normal decimal input
- `null` clears a value
- pressing Enter keeps the current value

For the auto-started `systemd` service, the serial settings menu is intentionally hardcoded in [rover/console.py](/Users/mustafa/Documents/GitHub/crane-rover/rover/console.py:1) so the console transport cannot be changed from `config.yaml`.

The current hardcoded console settings are:

- `CONSOLE_PORT = "/dev/ttyGS0"`
- `CONSOLE_BAUDRATE = 115200`
- `CONSOLE_READ_TIMEOUT_SEC = 0.2`
- `CONSOLE_WRITE_TIMEOUT_SEC = 0.2`

Behavior:

- logs and status are mirrored to that serial port while the service runs normally
- pressing any key on that serial connection opens the numbered settings menu
- saving from the menu rewrites `config.yaml` and restarts the rover process
- the hardcoded console port must be different from `serial.port`, because `serial.port` is used for the GNSS receiver

### Wi-Fi NetworkManager Permission

If Wi-Fi auto-selection logs `Not authorized` or another NetworkManager permission error, run this on the rover after updating the repo. It installs the included polkit rule so the `pi` user can control NetworkManager through `nmcli`:

```bash
cd /home/pi/crane-rover
sudo cp systemd/49-crane-rover-networkmanager.rules /etc/polkit-1/rules.d/49-crane-rover-networkmanager.rules
sudo systemctl restart polkit
```

If the rover app is running as the systemd service, restart it after installing the rule:

```bash
sudo systemctl restart crane-rover.service
```

### Example `config.yaml`

```yaml
serial:
  port: /dev/serial0
  baudrate: 115200

wifi:
  enabled: false
  interface: wlan0
  network1Ssid: ""
  network1Password: ""
  network2Ssid: ""
  network2Password: ""
  network3Ssid: ""
  network3Password: ""
  network4Ssid: ""
  network4Password: ""

rtcm:
  mode: ntrip

ntrip:
  host: your.ntrip.caster.host
  port: 2101
  mountpoint: your_mountpoint
  username: your_username
  password: your_password
  ggaForwardIntervalSec: 15

tcp:
  host: 192.168.1.50
  port: 9000

logging:
  level: INFO

status:
  enabled: true
  mode: normal
  intervalSec: 0.5

battery:
  enabled: true
  driver: waveshare-ups-hat-c
  i2cBus: 1
  i2cAddress: 0x43
  minVoltageV: 3.0
  maxVoltageV: 4.2

peerUdp:
  enabled: true
  deviceId: rover-01
  port: 5005
  broadcastHost: 255.255.255.255
  bufferDistanceM: 0.0
  extraTargets: []

blynk:
  enabled: true
  broker: blynk.cloud
  port: 8883
  username: device
  authToken: your_blynk_device_auth_token
  templateId: TMPLxxxxxxx
  firmwareVersion: 0.1.0
  publishIntervalSec: 1.0

mqtt:
  enabled: false
  broker: 192.168.1.50
  port: 1883
  username: ""
  password: ""
  topic: batch_ds
  publishIntervalSec: 0.1

web:
  enabled: false
  host: 0.0.0.0
  port: 8080
  safeDistanceThresholdM: 25.0
```

## Config Reference

### `serial`

- `port`
  Serial device path for the GNSS receiver. On Raspberry Pi this is often `/dev/serial0`.
- `baudrate`
  UART speed for the GNSS receiver.

### `wifi`

- `enabled`
  Enables preferred Wi-Fi selection and failover monitoring.
- `interface`
  Wi-Fi interface name, usually `wlan0`.
- `network1Ssid` to `network4Ssid`
  Preferred SSIDs in priority order.
- `network1Password` to `network4Password`
  Passwords paired with the configured SSIDs.

### `rtcm`

- `mode`
  RTCM source mode. Use `ntrip` for caster login or `tcp` for a raw RTCM stream on the local network.

### `ntrip`

- `host`
  NTRIP caster hostname or IP.
- `port`
  NTRIP caster port, usually `2101`.
- `mountpoint`
  Correction stream name.
- `username`
  NTRIP username.
- `password`
  NTRIP password.
- `ggaForwardIntervalSec`
  Optional override for GGA uplink cadence. The default is intentionally low at `15` seconds for public caster friendliness.

NTRIP reconnect safety timing is intentionally hard-coded in the application for public-caster use:

- reconnects never start faster than `10` seconds after a failed attempt
- retries back off through `10`, `20`, `40`, `60`, then `120` seconds and stay capped at `120`
- the consecutive-failure counter is only reset after at least `60` seconds of stable RTCM reception, not immediately after the HTTP/NTRIP handshake
- transient socket, timeout, or network failures keep retrying automatically with the capped backoff
- obvious permanent failures such as `401`, `403`, `404`, rejected mountpoints, or SOURCETABLE responses enter lockout and require the existing manual web reconnect action

### `tcp`

- `host`
  TCP RTCM source hostname or IP for `tcp` mode.
- `port`
  TCP RTCM source port for `tcp` mode.

### `logging`

- `level`
  Standard Python logging level such as `DEBUG`, `INFO`, or `WARNING`.

### `status`

- `enabled`
  Whether the terminal status printer thread runs.
- `mode`
  `normal` for operator view or `debug` for full diagnostics.
- `intervalSec`
  Seconds between status refreshes. Decimal values are allowed. Very low values can flood the terminal and serial console.

### `battery`

When using a Waveshare UPS HAT (C):

- `enabled`
  Enables battery monitoring thread.
- `driver`
  Use `waveshare-ups-hat-c`.
- `i2cBus`
  Usually `1` on Raspberry Pi.
- `i2cAddress`
  Usually `0x43` for this HAT.
- `minVoltageV`
  Battery empty reference for percentage estimation.
- `maxVoltageV`
  Battery full reference for percentage estimation.

### `peerUdp`

This controls peer-to-peer rover awareness.

- `enabled`
  Enables UDP broadcast and receive.
- `deviceId`
  Unique ID for this rover. Every rover must have a different one.
- `port`
  UDP port shared by all rovers.
- `broadcastHost`
  Usually `255.255.255.255`.
- `bufferDistanceM`
  Straight-line antenna-to-edge buffer distance in meters for this rover. The app subtracts this rover's value plus the peer rover's value from the center-to-center GNSS separation.
- `extraTargets`
  Optional list of unicast peer IP addresses. This is typically used for ZeroTier peer addresses.

### `blynk`

- `enabled`
  Enables MQTT publishing to Blynk.
- `broker`
  Usually `blynk.cloud`, unless you use a regional endpoint.
- `port`
  `8883` enables TLS for Blynk. `1883` uses plain MQTT for a local dashboard broker.
- `username`
  Usually `device`.
- `authToken`
  Your Blynk device token.
- `templateId`
  Blynk template ID.
- `firmwareVersion`
  Version string reported to Blynk.
- `publishIntervalSec`
  Seconds between publishes. Decimal values are allowed. The default is `1.0`.

### `web`

- `enabled`
  Enables the lightweight operator web viewer.
- `host`
  Bind address for the viewer, usually `0.0.0.0`.
- `port`
  HTTP port for the viewer, default `8080`.
- `safeDistanceThresholdM`
  Distance threshold used by the viewer. Values above it display `SAFE` in green, and values at or below it display `DANGER` in red.

### `mqtt`

- `enabled`
  Enables a second MQTT publisher in parallel with Blynk.
- `broker`
  Hostname or IP address for the second MQTT broker.
- `port`
  Broker port. `8883` enables TLS automatically, matching the Blynk behavior. `1883` uses plain MQTT.
- `username`
  Optional MQTT username.
- `password`
  Optional MQTT password.
- `topic`
  Topic used for the second MQTT payload stream. The payload matches the Blynk publish payload.
- `publishIntervalSec`
  Seconds between publishes. Decimal values are allowed. The default is `0.1` for faster local dashboard updates.

## Running the Rover

Run with an explicit config path:

```bash
python3 main.py config.yaml
```

Or use the default config filename:

```bash
python3 main.py
```

Stop with `Ctrl+C`.

## Run As A Service

If you want the rover to start automatically after the Pi boots, use `systemd`.

This repo includes a service file at [systemd/crane-rover.service](/Users/mustafa/Documents/GitHub/crane-rover/systemd/crane-rover.service).

Important assumptions in that unit:

- the project lives at `/home/pi/crane-rover`
- the Python virtual environment is `/home/pi/crane-rover/.venv`
- the service runs as user `pi`
- the config file is `/home/pi/crane-rover/config.yaml`

If your paths or username are different, edit the service file before installing it.

### Install the service

From the repo root on the Pi:

```bash
sudo cp systemd/crane-rover.service /etc/systemd/system/crane-rover.service
sudo systemctl daemon-reload
sudo systemctl enable crane-rover.service
sudo systemctl start crane-rover.service
```

### Check service status

```bash
sudo systemctl status crane-rover.service
```

### View logs

```bash
journalctl -u crane-rover.service -f
```

### Restart after config or code changes

```bash
sudo systemctl restart crane-rover.service
```

### Stop or disable the service

```bash
sudo systemctl stop crane-rover.service
sudo systemctl disable crane-rover.service
```

### Why this works after power loss

The service unit uses:

- `WantedBy=multi-user.target`
  So it starts during normal boot.
- `Restart=always`
  So `systemd` restarts it if the process exits or crashes.
- `RestartSec=5`
  Adds a short delay before restart attempts.

## Terminal Status Output

The status screen shows:

- local latitude, longitude, altitude
- satellites
- HDOP
- local fix / RTK mode
- estimated local horizontal accuracy
- RTCM source status and RTCM activity
- battery level, voltage, current, power, and battery status
- peer broadcast and receive timing
- nearest-peer style peer lines

Peer lines include:

- `safe`
  Conservative safety distance
- `raw`
  Buffer-adjusted straight-line distance after subtracting both rover buffer distances
- `uncertainty`
  Relative distance uncertainty
- `acc`
  Peer rover’s own estimated horizontal accuracy
- `FRESH` or `STALE`
  Whether the latest peer message is still considered valid

## Distance and Safety Model

### Raw distance

Center-to-center distance is calculated from latitude and longitude only.

Altitude is broadcast for reference, but it is not used in the distance calculation.

### Buffer distance

Each rover can advertise its own `peerUdp.bufferDistanceM`.

The app subtracts both rover buffer distances from the center-to-center GNSS separation:

```text
raw_distance = max(0, center_distance - local_buffer_distance - peer_buffer_distance)
```

This lets you treat the antenna location as a simple straight-line inset from the crane edge without introducing heading or X/Y offsets.

### Accuracy

Each rover still gets an estimated horizontal accuracy from its fix mode:

- `NO FIX` -> no usable accuracy
- `GNSS FIX` -> default `3.0 m`
- `DGPS` -> default `1.0 m`
- `RTK FLOAT` -> default `0.2 m`
- `RTK FIXED` -> default `0.02 m`

### Uncertainty / safety tolerance

Safety tolerance is now loaded from [rover/safety_tolerance_lookup.json](/Users/mustafa/Documents/GitHub/crane-rover/rover/safety_tolerance_lookup.json).

For any local/peer fix-mode pair not present in that lookup, the app falls back to root-sum-square:

```text
fallback_tolerance = sqrt(local_accuracy^2 + peer_accuracy^2)
```

### Safe distance

The conservative safety distance is:

```text
safe_distance = max(0, raw_distance - tolerance)
```

This means the system assumes the cranes may be closer than the buffer-adjusted GNSS separation suggests.

### Stale message rule

If the latest peer message for a rover is older than the built-in freshness threshold, the distance is no longer treated as valid.

That is the safety cutoff for peer tracking.

## Published Telemetry Format

The rover publishes one compact JSON payload with local rover telemetry and the current nearest-rover safety data.

That same payload can be sent to Blynk and, if enabled, to a second MQTT broker at the same time.

| Rover 1 | Rover 2 |
| --- | --- |
| `rover_name` | `nearest_peer_id` |
| `latitude` | `nearest_peer_distance_m` |
| `longitude` | `nearest_peer_safe_distance_m` |
| `altitude_m` | `nearest_peer_uncertainty_m` |
| `position` | `nearest_peer_combined_accuracy_m` |
| `satellites` | `nearest_peer_accuracy_m` |
| `hdop` | `nearest_peer_fix_mode` |
| `rtcm_age_sec` |  |
| `fix_mode` |  |
| `ntrip_status` |  |
| `rtcm_source_mode` |  |
| `battery_percent` |  |
| `battery_voltage_v` |  |
| `battery_current_a` |  |
| `battery_power_w` |  |
| `battery_status` |  |
| `battery_present` |  |
| `local_accuracy_m` |  |

`rover_name` is taken from `peerUdp.deviceId`.

`nearest_peer_combined_accuracy_m` is kept as a compatibility key and has the same value as `nearest_peer_uncertainty_m`.

### Suggested dashboard fields for safety view

- `nearest_peer_distance_m` as `Double`
- `nearest_peer_safe_distance_m` as `Double`
- `nearest_peer_uncertainty_m` as `Double`
- `nearest_peer_accuracy_m` as `Double`
- `nearest_peer_fix_mode` as `Integer`
- `nearest_peer_id` as `String`
- `local_accuracy_m` as `Double`

Recommended units:

- distance, safe distance, uncertainty, and accuracy -> `m`

Recommended enum mapping for `nearest_peer_fix_mode`:

- `0` = unknown / no fix
- `1` = GNSS FIX
- `2` = DGPS
- `3` = RTK FLOAT
- `4` = RTK FIXED

## Waveshare UPS HAT (C)

The default battery integration targets the Waveshare UPS HAT (C), using the onboard INA219 over I2C.

Useful checks on the Pi:

```bash
sudo i2cdetect -y 1
```

You should usually see the device at `0x43`.

One-shot Python battery test:

```bash
python3 - <<'PY'
from rover.battery import read_waveshare_ups_hat_c_snapshot

cfg = {
    "i2cBus": 1,
    "i2cAddress": "0x43",
    "minVoltageV": 3.0,
    "maxVoltageV": 4.2,
}
print(read_waveshare_ups_hat_c_snapshot(cfg))
PY
```

## Troubleshooting

### No battery values

Check:

- `battery.enabled: true` exists in `config.yaml`
- I2C is enabled
- `0x43` appears in `sudo i2cdetect -y 1`
- `smbus2` is installed in the same Python environment

### Battery reader works in a test but not in the app

Check:

- the app is using the expected `config.yaml`
- the virtual environment is active
- the battery thread is actually enabled by config

### No peer distance appears

Check:

- `peerUdp.enabled: true`
- each rover has a unique `peerUdp.deviceId`
- all rovers use the same `peerUdp.port`
- the rovers are on the same network
- UDP broadcast is allowed on that network
- peer messages are arriving frequently enough to remain fresh

### Peer data is stale

This threshold is built into the app. If you need different stale timing behavior, change it in code.

### Blynk is not updating

Check:

- `blynk.enabled: true`
- correct `authToken`
- correct broker endpoint
- outbound MQTT/TLS connectivity

### Second MQTT broker is not updating

Check:

- `mqtt.enabled: true`
- `mqtt.broker` and `mqtt.port`
- `mqtt.topic`
- optional `mqtt.username` and `mqtt.password`
- `mqtt.publishIntervalSec`
- outbound MQTT/TLS connectivity

### Status output is too fast or too slow

Check:

- `status.intervalSec`
- lower values increase stdout and serial console traffic significantly
- `0.5` is a reasonable fast default
- `0.1` is possible, but it will generate a very noisy full-screen status stream

If needed, raise logging level to debug:

```yaml
logging:
  level: DEBUG
```

## Development Notes

- The code uses threads, not `asyncio`.
- Shared state is protected by locks in `rover/state.py`.
- The nearest-peer logic is intentionally compact for operator use and MQTT dashboards.
- The system currently computes 2D horizontal distance only.

## Suggested `.gitignore`

```gitignore
config.yaml
.venv/
__pycache__/
*.pyc
```
