# RTK Stage 1

Minimal rover script for:
- reading NMEA from the GNSS receiver
- printing NMEA to stdout
- connecting to an NTRIP caster
- forwarding RTCM corrections into the receiver over UART

## Files

- `main.py`
- `requirements.txt`
- `config.example.yaml`
- `config.yaml` (local only, not committed)

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

logging:
  level: INFO
```

## Run

```bash
python main.py config.yaml
```

Or:

```bash
python main.py
```

## Notes

- recommended serial port: `/dev/serial0`
- this stage does not send GGA back to the caster yet
- if NTRIP drops, the script will retry automatically

## Suggested .gitignore

```gitignore
config.yaml
.venv/
__pycache__/
*.pyc
```
