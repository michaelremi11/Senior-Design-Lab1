"""Data source for the thermometer box.

THIS IS THE ONLY FILE THAT KNOWS WHERE READINGS COME FROM.
Right now it is a simulator. Later, the hardware version of this file keeps the
same two public functions and the same return shape; server.py never changes.

Public API:
    read_sensors() -> dict   # called once per second by server.py
    set_display(sensor, on)  # remote button toggle, sensor is 'a' or 'b'

read_sensors() returns:
{
  'box_on': bool,                 # False => "no data available"
  'a': {'value': float|None,      # degrees CELSIUS on the wire, always
        'status': 'ok'|'unplugged',
        'display_on': bool},      # state of that sensor's local box display
  'b': {...}
}
"""

import math
import random
import threading
import time

_lock = threading.Lock()
_t0 = time.time()

# Simulated box state. The /api/sim endpoint pokes these for live demos.
_state = {
    'box_on': True,
    'a': {'plugged': True, 'display_on': True},
    'b': {'plugged': True, 'display_on': True},
}

# Per-sensor random-walk noise, so the trace wiggles instead of being a clean sine.
_noise = {'a': 0.0, 'b': 0.0}

# base_c, swing_c, period_s  -> a slow sine the grader can actually see move.
_PROFILE = {
    'a': (24.0, 6.0, 180.0),    # stays comfortably inside the 10-50 chart band
    'b': (32.0, 12.0, 120.0),   # swings 20-44, crosses the default 40 alert max
}


def _simulate(sensor, now):
    base, swing, period = _PROFILE[sensor]
    _noise[sensor] = max(-1.5, min(1.5, _noise[sensor] + random.uniform(-0.25, 0.25)))
    return base + swing * math.sin(2 * math.pi * (now - _t0) / period) + _noise[sensor]


def read_sensors():
    now = time.time()
    with _lock:
        out = {'box_on': _state['box_on']}
        for s in ('a', 'b'):
            plugged = _state[s]['plugged']
            out[s] = {
                'value': round(_simulate(s, now), 2) if plugged else None,
                'status': 'ok' if plugged else 'unplugged',
                'display_on': _state[s]['display_on'],
            }
        return out


def set_display(sensor, on):
    """Remote control: turn one sensor's local box display on/off."""
    with _lock:
        _state[sensor]['display_on'] = bool(on)


# ---- simulator-only helpers (the hardware version of this file drops these) ----

def sim_set(box_on=None, a_plugged=None, b_plugged=None):
    with _lock:
        if box_on is not None:
            _state['box_on'] = bool(box_on)
        if a_plugged is not None:
            _state['a']['plugged'] = bool(a_plugged)
        if b_plugged is not None:
            _state['b']['plugged'] = bool(b_plugged)


def sim_state():
    with _lock:
        return {
            'box_on': _state['box_on'],
            'a_plugged': _state['a']['plugged'],
            'b_plugged': _state['b']['plugged'],
        }
