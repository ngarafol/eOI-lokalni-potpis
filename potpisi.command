#!/bin/bash
# Dvoklik / prevlačenje PDF-a na ovu datoteku = lokalno potpisivanje PDF-a karticom.
cd "$(dirname "$0")" || exit 1
exec .venv/bin/python potpisi-pdf.py "$@"
