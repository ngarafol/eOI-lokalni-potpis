#!/bin/bash
# Linux/macOS: GUI za lokalno potpisivanje PDF-a (pokreni iz mape projekta).
cd "$(dirname "$0")" || exit 1
[ -x .venv/bin/python ] || { echo "Nema .venv - vidi README (Instalacija)."; exit 1; }
exec .venv/bin/python potpisi-gui.py "$@"
