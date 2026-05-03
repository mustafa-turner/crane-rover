# Crane Rover

Python rover application for Raspberry Pi based crane positioning nodes.

This project is built around a GNSS receiver, an NTRIP correction link, an optional Waveshare UPS HAT (C), UDP peer broadcasting between rovers, and Blynk MQTT publishing.

## What It Does

Each rover can:

- read NMEA from a GNSS receiver over serial
- connect to an NTRIP caster and forward RTCM corrections back into the receiver
- track rover fix mode, HDOP, RTCM activity, and battery telemetry
- broadcast its latest timestamped position and accuracy to nearby rover devices over UDP
- compute distance to the nearest peer rover
- compute a conservative safety distance by subtracting uncertainty from raw distance
- publish local rover telemetry and nearest-peer safety data to Blynk
- print a live operator status view in the terminal
- expose a simple keyboard-driven settings menu when running on an attached terminal

## High-Level Architecture

`main.py` starts a set of worker threads:

- `rover/gnss.py`
  Reads NMEA sentences from the GNSS receiver.
- `rover/ntrip.py`
  Connects to the NTRIP caster and forwards RTCM data to the receiver.
- `rover/status.py`
  Prints current rover state to the terminal.
- `rover/battery.py`
  Reads battery telemetry, usually from a Waveshare UPS HAT (C) INA219 over I2C.
- `rover/peer_udp.py`
  Broadcasts rover state to peers and listens for peer messages.
- `rover/blynk.py`
  Publishes rover telemetry to Blynk over MQTT.
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

1. Open the GNSS serial port.
2. Start reading NMEA from the receiver.
3. Start NTRIP and inject RTCM corrections into the receiver.
4. Update shared rover state from GNSS and RTCM data.
5. Optionally read UPS battery telemetry.
6. Broadcast local rover state to peers over UDP.
7. Receive peer state and calculate nearest-peer distance and safety margin.
8. Print everything to the terminal.
9. Optionally publish a compact telemetry payload to Blynk.
10. If running on a real TTY, enter the settings menu when any key is pressed.

## Project Files

- `main.py`
- `rover/config.py`
- `rover/state.py`
- `rover/gnss.py`
- `rover/ntrip.py`
- `rover/battery.py`
- `rover/peer_udp.py`
- `rover/blynk.py`
- `rover/status.py`
- `requirements.txt`
- `config.example.yaml`
- `config.yaml`
  Local-only file, not committed.

## Requirements

Typical environment:

- Raspberry Pi
- Python 3
- GNSS receiver connected by UART/serial
- NTRIP caster credentials
- network access for NTRIP and optionally Blynk
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

Then edit:

```bash
nano config.yaml
```

If the rover runs in an interactive terminal, the app supports live config editing:

- leave it idle to watch logs and status
- press any key to open the settings menu
- enter section numbers such as `1` for `serial` or `2` for `ntrip`
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

Behavior:

- logs and status are mirrored to that serial port while the service runs normally
- pressing any key on that serial connection opens the numbered settings menu
- saving from the menu rewrites `config.yaml` and restarts the rover process
- the hardcoded console port must be different from `serial.port`, because `serial.port` is used for the GNSS receiver

### Example `config.yaml`

```yaml
serial:
  port: /dev/serial0
  baudrate: 115200

ntrip:
  host: your.ntrip.caster.host
  port: 2101
  mountpoint: your_mountpoint
  username: your_username
  password: your_password
  connectTimeoutSec: 10
  readTimeoutSec: 15
  reconnectDelaySec: 5
  chunkSize: 1024
  ggaForwardEnabled: true
  ggaForwardIntervalSec: 5
  recvPollTimeoutSec: 1.0
  rtcmLogIntervalSec: 10

logging:
  level: INFO

status:
  enabled: true
  printIntervalSec: 2

battery:
  enabled: true
  driver: waveshare-ups-hat-c
  pollIntervalSec: 10
  i2cBus: 1
  i2cAddress: 0x43
  shuntOhms: 0.1
  maxCurrentA: 3.2
  minVoltageV: 3.0
  maxVoltageV: 4.2

peerUdp:
  enabled: true
  deviceId: rover-01
  port: 5005
  broadcastHost: 255.255.255.255
  listenHost: ""
  broadcastIntervalSec: 1.0
  recvPollTimeoutSec: 0.2
  maxPeerMessageAgeSec: 2.0

blynk:
  enabled: true
  broker: blynk.cloud
  port: 8883
  username: device
  authToken: your_blynk_device_auth_token
  templateId: TMPLxxxxxxx
  firmwareVersion: 0.1.0
  publishIntervalSec: 2
  useTls: true
  keepaliveSec: 45
```

## Config Reference

### `serial`

- `port`
  Serial device path for the GNSS receiver. On Raspberry Pi this is often `/dev/serial0`.
- `baudrate`
  UART speed for the GNSS receiver.

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
- `connectTimeoutSec`
  TCP connection timeout.
- `readTimeoutSec`
  Maximum allowed time without RTCM before reconnecting.
- `reconnectDelaySec`
  Delay before retrying after failure.
- `chunkSize`
  Number of bytes read from the caster per socket read.
- `ggaForwardEnabled`
  Whether the rover sends its latest GGA sentence to the caster.
- `ggaForwardIntervalSec`
  How often GGA is resent while connected.
- `recvPollTimeoutSec`
  Socket polling timeout.
- `rtcmLogIntervalSec`
  How often recent RTCM types are logged.

### `logging`

- `level`
  Standard Python logging level such as `DEBUG`, `INFO`, or `WARNING`.

### `status`

- `enabled`
  Whether the terminal status printer thread runs.
- `printIntervalSec`
  How often the status screen is printed to the terminal.

### `battery`

When using a Waveshare UPS HAT (C):

- `enabled`
  Enables battery monitoring thread.
- `driver`
  Use `waveshare-ups-hat-c`.
- `pollIntervalSec`
  How often battery telemetry is refreshed.
- `i2cBus`
  Usually `1` on Raspberry Pi.
- `i2cAddress`
  Usually `0x43` for this HAT.
- `shuntOhms`
  INA219 shunt value, normally `0.1`.
- `maxCurrentA`
  Used to derive INA219 calibration.
- `minVoltageV`
  Battery empty reference for percentage estimation.
- `maxVoltageV`
  Battery full reference for percentage estimation.

Fallback mode:

- `driver: sysfs`
  Uses Linux power supply files instead of INA219.
- `basePath`
  Path such as `/sys/class/power_supply/battery`.

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
- `listenHost`
  Usually empty string `""` to bind all interfaces.
- `broadcastIntervalSec`
  How often this rover transmits its peer message.
- `recvPollTimeoutSec`
  UDP receive polling timeout.
- `maxPeerMessageAgeSec`
  Safety timeout. If a peer message is older than this, distance is treated as stale and ignored.

### `blynk`

- `enabled`
  Enables MQTT publishing to Blynk.
- `broker`
  Usually `blynk.cloud`, unless you use a regional endpoint.
- `port`
  Usually `8883` with TLS.
- `username`
  Usually `device`.
- `authToken`
  Your Blynk device token.
- `templateId`
  Blynk template ID.
- `firmwareVersion`
  Version string reported to Blynk.
- `publishIntervalSec`
  Publish interval in seconds.
- `useTls`
  Enables TLS.
- `keepaliveSec`
  MQTT keepalive period.

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
- NTRIP connection status and RTCM activity
- battery level, voltage, current, power, and battery status
- peer broadcast and receive timing
- nearest-peer style peer lines

Peer lines include:

- `safe`
  Conservative safety distance
- `raw`
  Raw latitude/longitude distance
- `uncertainty`
  Relative distance uncertainty
- `acc`
  Peer rover’s own estimated horizontal accuracy
- `FRESH` or `STALE`
  Whether the latest peer message is still considered valid

## Distance and Safety Model

### Raw distance

Distance is calculated from latitude and longitude only.

Altitude is broadcast for reference, but it is not used in the distance calculation.

### Accuracy

Each rover gets an estimated horizontal accuracy from its fix mode:

- `NO FIX` -> no usable accuracy
- `GNSS FIX` -> default `5.0 m`
- `DGPS` -> default `1.5 m`
- `RTK FLOAT` -> default `0.5 m`
- `RTK FIXED` -> default `0.02 m`

### Uncertainty

Relative uncertainty between two rovers is calculated as root-sum-square:

```text
uncertainty = sqrt(local_accuracy^2 + peer_accuracy^2)
```

### Safe distance

The conservative safety distance is:

```text
safe_distance = max(0, raw_distance - uncertainty)
```

This means the system assumes the cranes may be closer than the raw GNSS separation suggests.

### Stale message rule

If the latest peer message for a rover is older than `peerUdp.maxPeerMessageAgeSec`, the distance is no longer treated as valid.

That is the safety cutoff for peer tracking.

## UDP Peer Message Contents

Each rover broadcasts a JSON message containing:

- schema name
- device ID
- send timestamp
- latitude
- longitude
- altitude
- fix label
- fix quality
- estimated local accuracy

Only the latest message per peer is kept.
Older timestamps from the same peer are ignored.

## Blynk Payload

The rover publishes a compact payload that includes local telemetry plus nearest-peer information.

### Local / battery fields

- `latitude`
- `longitude`
- `altitude_m`
- `position`
- `satellites`
- `hdop`
- `rtcm_age_sec`
- `fix_mode`
- `ntrip_status`
- `battery_percent`
- `battery_voltage_v`
- `battery_current_a`
- `battery_power_w`
- `battery_status`
- `battery_present`
- `local_accuracy_m`

### Nearest-peer fields

- `nearest_peer_distance_m`
- `nearest_peer_safe_distance_m`
- `nearest_peer_uncertainty_m`
- `nearest_peer_combined_accuracy_m`
  Compatibility key, same value as `nearest_peer_uncertainty_m`
- `nearest_peer_accuracy_m`
- `nearest_peer_fix_mode`
- `nearest_peer_id`

### Suggested Blynk datastreams for nearest-peer safety

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
    "shuntOhms": 0.1,
    "maxCurrentA": 3.2,
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
- peer messages are arriving more frequently than `maxPeerMessageAgeSec`

### Peer data is stale

Increase one or both:

- `peerUdp.broadcastIntervalSec`
- `peerUdp.maxPeerMessageAgeSec`

But be careful: raising the stale timeout too much weakens the safety rule.

### Blynk is not updating

Check:

- `blynk.enabled: true`
- correct `authToken`
- correct broker endpoint
- outbound MQTT/TLS connectivity

If needed, raise logging level to debug:

```yaml
logging:
  level: DEBUG
```

## Development Notes

- The code uses threads, not `asyncio`.
- Shared state is protected by locks in `rover/state.py`.
- The nearest-peer logic is intentionally compact for operator use and Blynk dashboards.
- The system currently computes 2D horizontal distance only.

## Suggested `.gitignore`

```gitignore
config.yaml
.venv/
__pycache__/
*.pyc
```
