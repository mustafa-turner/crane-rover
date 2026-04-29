# RTK Stage 1

Minimal rover app for:
- reading NMEA from the GNSS receiver
- printing rover status to the terminal
- connecting to an NTRIP caster
- forwarding RTCM corrections into the receiver over UART
- reading UPS battery status from the Waveshare UPS HAT (C) over I2C
- publishing rover status to Blynk over MQTT

## Files

- `main.py`
- `rover/config.py` for config loading and logging setup
- `rover/state.py` for shared rover state, GGA storage, and RTCM inspection
- `rover/gnss.py` for serial access and NMEA parsing
- `rover/ntrip.py` for NTRIP connection and RTCM forwarding
- `rover/battery.py` for UPS battery monitoring via INA219 / sysfs fallback
- `rover/blynk.py` for MQTT publishing
- `rover/status.py` for terminal status output
- `requirements.txt`
- `config.example.yaml`
- `config.yaml` (local only, not committed)

Each Python file now has one main responsibility, so the code stays compact without keeping every concern in one large script.

## Create venv

```bash
python3 -m venv .venv
source .venv/bin/activate
```

## Install dependencies

```bash
pip install --upgrade pip
pip install -r requirements.txt
```

## Create config.yaml

Since `config.yaml` is in `.gitignore`, create it from the example:

```bash
cp config.example.yaml config.yaml
nano config.yaml
```

Example:

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
```

## Run

```bash
python3 main.py config.yaml
```

Or:

```bash
python3 main.py
```

## Notes

- recommended serial port: `/dev/serial0`
- the script forwards the latest GGA sentence back to the caster when `ggaForwardEnabled` is `true`
- the status output now shows whether the incoming RTCM stream contains station messages (`1005`/`1006`) and observation MSM messages (`107x`/`108x`/`109x`/`111x`/`112x`)
- for the Waveshare UPS HAT (C), battery data is read through the onboard INA219 over I2C, usually at address `0x43`
- if I2C battery reads fail on Raspberry Pi, install `python3-smbus` or keep `smbus2` in the Python environment
- the status output also shows battery level, voltage, current, power, status, and battery read age when `battery.enabled` is `true`
- the Blynk payload includes `battery_percent`, `battery_voltage_v`, `battery_current_a`, `battery_power_w`, `battery_status`, and `battery_present`
- if NTRIP drops, the script will retry automatically
- `main.py` is now only the bootstrap entrypoint; protocol logic lives under `rover/`

## Suggested .gitignore

```gitignore
config.yaml
.venv/
__pycache__/
*.pyc
```
