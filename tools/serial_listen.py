#!/usr/bin/env python3
"""Print every byte that arrives on one or more serial ports at 19200 baud, never sending.

Finds which port the S6000's SAS line reaches: a machine that has heard no poll for 5 s
"chirps" its address every 200 ms (SAS 6.02 §4.2), e.g. `01` for address 1.

  serial_listen.py /dev/ttyUSB0 /dev/ttyUSB1 /dev/ttyUSB2 /dev/ttyUSB3
"""
import select
import sys
import time

import serial

ports = {serial.Serial(d, 19200, timeout=0): d for d in sys.argv[1:]}
print("listening on", ", ".join(ports.values()), flush=True)
while True:
    for port in select.select(list(ports), [], [])[0]:
        print(time.strftime("%H:%M:%S"), ports[port], port.read(256).hex(" "), flush=True)
