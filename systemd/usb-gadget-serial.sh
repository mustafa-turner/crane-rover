#!/bin/bash
set -euo pipefail

GADGET_ROOT="/sys/kernel/config/usb_gadget/crane"
GADGET_NAME="crane"
SERIAL_NUMBER="${USB_GADGET_SERIAL_NUMBER:-0001}"
MANUFACTURER="${USB_GADGET_MANUFACTURER:-Crane Rover}"
PRODUCT="${USB_GADGET_PRODUCT:-Crane Rover Serial Console}"
CONFIG_LABEL="${USB_GADGET_CONFIG_LABEL:-Serial Console}"
VENDOR_ID="${USB_GADGET_VENDOR_ID:-0x1d6b}"
PRODUCT_ID="${USB_GADGET_PRODUCT_ID:-0x0104}"
UDC_NAME="${USB_GADGET_UDC:-}"

modprobe libcomposite

mkdir -p /sys/kernel/config/usb_gadget

if [[ -d "${GADGET_ROOT}" && -n "$(ls -A "${GADGET_ROOT}" 2>/dev/null)" ]]; then
  if [[ -f "${GADGET_ROOT}/UDC" ]]; then
    echo "" > "${GADGET_ROOT}/UDC" || true
  fi
fi

mkdir -p "${GADGET_ROOT}"
cd "${GADGET_ROOT}"

echo "${VENDOR_ID}" > idVendor
echo "${PRODUCT_ID}" > idProduct
echo 0x0100 > bcdDevice
echo 0x0200 > bcdUSB

mkdir -p strings/0x409
echo "${SERIAL_NUMBER}" > strings/0x409/serialnumber
echo "${MANUFACTURER}" > strings/0x409/manufacturer
echo "${PRODUCT}" > strings/0x409/product

mkdir -p configs/c.1/strings/0x409
echo "${CONFIG_LABEL}" > configs/c.1/strings/0x409/configuration
echo 250 > configs/c.1/MaxPower

mkdir -p functions/acm.usb0
ln -sfn functions/acm.usb0 configs/c.1/functions.acm.usb0

if [[ -z "${UDC_NAME}" ]]; then
  UDC_NAME="$(ls /sys/class/udc | head -n 1)"
fi

if [[ -z "${UDC_NAME}" ]]; then
  echo "No USB Device Controller found. This Pi/port may not support gadget mode." >&2
  exit 1
fi

echo "${UDC_NAME}" > UDC

