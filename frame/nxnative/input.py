#!/usr/bin/env python3
"""Runs ON Frame: inject a button through the selected Lepton input FIFO.

Example: ssh frame python3 - A 200 < frame/nxnative/input.py
Android's input/cmd shell service and InputManager injection do not deliver
these events in the inspected Lepton image. Use the same FIFO as its Wayland
keyboard bridge; the app still receives real AInputQueue events.
"""
import argparse
import json
from pathlib import Path
import struct
import subprocess
import time

KEYS = {'A': 305, 'B': 304, 'X': 308, 'Y': 307, 'L': 310, 'R': 311,
        'ZL': 312, 'ZR': 313, 'Plus': 315, 'Minus': 314,
        'Up': 103, 'Down': 108, 'Left': 105, 'Right': 106,
        'MoveUp': 17, 'MoveLeft': 30, 'MoveDown': 31, 'MoveRight': 32}


def button(key, hold, title):
    if not isinstance(key, str) or key not in KEYS:
        raise ValueError('unknown key: ' + repr(key))
    if isinstance(hold, bool) or not isinstance(hold, int) or not 1 <= hold <= 10000:
        raise ValueError('hold must be 1..10000 milliseconds')
    if not isinstance(title, str) or len(title) != 16 or any(c not in '0123456789abcdefABCDEF' for c in title):
        raise ValueError('invalid title ID')
    package = 'com.nxnative.t' + title.lower()
    meta = json.loads((Path.home() / 'Applications/Android' / package / 'meta.json').read_text(encoding='utf-8'))
    if not isinstance(meta, dict) or meta.get('package') != package:
        raise ValueError('package metadata mismatch')
    instance = meta.get('instance')
    if isinstance(instance, str) and instance.isascii() and instance.isdigit():
        instance = int(instance)
    elif type(instance) is not int or instance < 0:
        raise ValueError('invalid instance metadata')
    container = 'lepton-steamlaunch-' + str(instance)
    child = subprocess.Popen(['podman', 'exec', '-i', container, '/system/bin/sh', '-c',
                              'cat > /dev/input/wl_keyboard_events'], stdin=subprocess.PIPE)
    def event(kind, code, value):
        now = time.monotonic()
        return struct.pack('<qqHHi', int(now), int(now % 1 * 1e6), kind, code, value)
    write_error = None
    try:
        child.stdin.write(event(1, KEYS[key], 1) + event(0, 0, 0))
        child.stdin.flush()
        time.sleep(hold / 1000)
    except OSError as exc:
        write_error = exc
    finally:
        try:
            if write_error is None:
                child.stdin.write(event(1, KEYS[key], 0) + event(0, 0, 0))
        except OSError as exc:
            write_error = exc
        finally:
            try:
                child.stdin.close()
            except OSError:
                pass
            rc = child.wait()
    if rc or write_error is not None:
        raise RuntimeError('Lepton input FIFO write failed')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('key', choices=KEYS)
    parser.add_argument('hold', nargs='?', type=int, default=200)
    parser.add_argument('--title', default='010093801237C000')
    args = parser.parse_args()
    button(args.key, args.hold, args.title)
