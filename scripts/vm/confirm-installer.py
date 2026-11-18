"""Acknowledge only the observed Subiquity confirmation on NetGuard's UTM serial port."""

import os
import select
import subprocess
import termios
import time
import tty

vm = "NetGuard-Ubuntu-ARM64"
query = f'tell application "UTM" to get address of serial port 1 of virtual machine named "{vm}"'
port = subprocess.check_output(["osascript", "-e", query], text=True).strip()
fd = os.open(port, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
saved = termios.tcgetattr(fd)
try:
    tty.setraw(fd, termios.TCSANOW)
    window = b""
    deadline = time.monotonic() + 600
    while time.monotonic() < deadline:
        if not select.select([fd], [], [], 1)[0]:
            continue
        data = os.read(fd, 8192)
        if not data:
            raise SystemExit("Serial port closed before installer confirmation")
        window = (window + data)[-8192:]
        if b"An error occurred. Press enter to start a shell" in window:
            raise SystemExit("Installer reported an error. Inspect the dedicated serial console.")
        if b"Continue with autoinstall? (yes|no)" in window:
            os.write(fd, b"yes\r")
            print("Confirmed the NetGuard guest installation.")
            break
    else:
        raise SystemExit("Timed out waiting for a recognized installer confirmation")
finally:
    termios.tcsetattr(fd, termios.TCSANOW, saved)
    os.close(fd)
