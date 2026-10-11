#!/bin/bash
# Dvoklik = GUI za lokalno potpisivanje PDF-a karticom.
cd "$(dirname "$0")" || exit 1
exec .venv/bin/python potpisi-gui.py
