#!/usr/bin/env python3
"""Swing TXD of one or more serial ports between mark (negative) and space (positive) every
3 s, using the break condition, so a multimeter shows which wire the adapter drives.

On the DB9 breakout, the terminal that swings is the adapter's transmit line: a straight
cable puts it on pin 3, a null-modem cable on pin 2. Bytes that arrive are printed.

  serial_txd_toggle.py /dev/ttyUSB1
"""
import select
import sys
import time

import serial

HALF_PERIOD = 3  # slow enough for a multimeter to settle

ports = {serial.Serial(d, 19200, timeout=0): d for d in sys.argv[1:]}
space, flip = False, 0.0
while True:
    if time.monotonic() >= flip:
        space = not space
        for port in ports:
            port.break_condition = space
        print(time.strftime("%H:%M:%S"), "TXD", "positive (space)" if space else "negative (mark)", flush=True)
        flip = time.monotonic() + HALF_PERIOD
    for port in select.select(list(ports), [], [], max(0.0, flip - time.monotonic()))[0]:
        print(time.strftime("%H:%M:%S"), ports[port], port.read(256).hex(" "), flush=True)
