#!/bin/sh
# Explicit operator evidence only. No service/driver/reboot mutations.
set -eu
exec /usr/bin/python3 -I "$(dirname "$0")/hardware_acceptance.py" "$@"
