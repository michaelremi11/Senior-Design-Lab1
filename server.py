#!/usr/bin/env python3
"""ECE:4880 Lab 1 backend.  Run:  python server.py   ->  http://localhost:8000

One thread samples sensors.read_sensors() once a second and appends to a
300-entry ring buffer per sensor (300 s of history, 1 point/s).  The HTTP
handler just serves that buffer as JSON.  Everything is degrees Celsius; the
browser does the F conversion.
"""

import collections
import json
import os
import smtplib
import socket
import sys
import threading
import time
from email.message import EmailMessage
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler

import sensors

sys.stdout.reconfigure(line_buffering=True)   # so [ALERT] lines show up live

HERE = os.path.dirname(os.path.abspath(__file__))
PORT = int(os.environ.get('PORT', 8000))
BUFFER_SECONDS = 300          # chart window
VALID_MIN_C = -10.0           # anything outside this is treated as a fault,
VALID_MAX_C = 63.0            # NOT as a reading (chart band is 10-50, separate thing)

SETTINGS_PATH = os.path.join(HERE, 'settings.json')
DEFAULT_SETTINGS = {
    'alerts_enabled': True,
    'min_c': 15.0,
    'max_c': 40.0,
    'msg_low': 'Lab 1 alert: temperature below minimum.',
    'msg_high': 'Lab 1 alert: temperature above maximum.',
    'destination': '',            # email address, or a carrier SMS gateway
                                  # address like 3195551234@vtext.com
    'cooldown_s': 60,             # min seconds between alerts for one sensor
}

_lock = threading.Lock()
history = {s: collections.deque(maxlen=BUFFER_SECONDS) for s in ('a', 'b')}
latest = {'box_on': False,
          'a': {'value': None, 'status': 'off', 'display_on': True},
          'b': {'value': None, 'status': 'off', 'display_on': True}}
alert_log = collections.deque(maxlen=20)     # shown in the UI as proof alerts fired
_alarm = {'a': None, 'b': None}              # 'low' | 'high' | None
_last_alert_at = {'a': 0.0, 'b': 0.0}


# ---------------------------------------------------------------- settings

def load_settings():
    s = dict(DEFAULT_SETTINGS)
    try:
        with open(SETTINGS_PATH) as f:
            s.update(json.load(f))
    except FileNotFoundError:
        pass
    except Exception as e:
        print('[settings] bad settings file, using defaults:', e)
    return s


def save_settings(s):
    with open(SETTINGS_PATH, 'w') as f:
        json.dump(s, f, indent=2)


settings = load_settings()


# ---------------------------------------------------------------- sampling

def take_sample():
    r = sensors.read_sensors()
    now = time.time()
    with _lock:
        box_on = bool(r.get('box_on'))
        latest['box_on'] = box_on
        for s in ('a', 'b'):
            v = r[s].get('value')
            if not box_on:
                status, value = 'off', None            # -> "no data available"
            elif r[s].get('status') != 'ok' or v is None \
                    or not (VALID_MIN_C <= float(v) <= VALID_MAX_C):
                status, value = 'unplugged', None      # -> "unplugged sensor"
            else:
                status, value = 'ok', round(float(v), 2)
            history[s].append({'t': now, 'v': value, 's': status})
            latest[s] = {'value': value, 'status': status,
                         'display_on': bool(r[s].get('display_on', True))}
    check_alerts()


def sample_loop():
    nxt = time.time()
    while True:
        try:
            take_sample()
        except Exception as e:                          # never kill the loop
            print('[sample] error:', e)
        nxt += 1.0
        time.sleep(max(0.0, nxt - time.time()))


# ---------------------------------------------------------------- alerts

def check_alerts():
    with _lock:
        cfg = dict(settings)
        snap = {s: dict(latest[s]) for s in ('a', 'b')}
    if not cfg['alerts_enabled']:
        return
    now = time.time()
    for s in ('a', 'b'):
        v = snap[s]['value']
        if snap[s]['status'] != 'ok' or v is None:
            _alarm[s] = None                # outage clears the latch
            continue
        if v > cfg['max_c']:
            state, msg = 'high', cfg['msg_high']
        elif v < cfg['min_c']:
            state, msg = 'low', cfg['msg_low']
        else:
            _alarm[s] = None
            continue
        if _alarm[s] == state:              # already alerted, don't spam
            continue
        if now - _last_alert_at[s] < cfg['cooldown_s']:
            continue
        _alarm[s] = state
        _last_alert_at[s] = now
        body = '%s  (sensor %s = %.2f C, limits %.1f..%.1f C)' % (
            msg, s.upper(), v, cfg['min_c'], cfg['max_c'])
        record = {'t': now, 'sensor': s, 'kind': state, 'text': body,
                  'to': cfg['destination'], 'sent': False}
        alert_log.appendleft(record)
        threading.Thread(target=send_alert, args=(cfg['destination'], body, record),
                         daemon=True).start()


def send_alert(destination, body, record):
    """SMTP if env vars are set, otherwise print. Both count as 'the alert fired'."""
    host = os.environ.get('SMTP_HOST')
    if not host or not destination:
        print('[ALERT -> %s] %s' % (destination or 'console', body))
        return
    try:
        msg = EmailMessage()
        msg['From'] = os.environ.get('ALERT_FROM', os.environ.get('SMTP_USER', ''))
        msg['To'] = destination
        msg['Subject'] = 'Thermometer alert'
        msg.set_content(body)
        with smtplib.SMTP(host, int(os.environ.get('SMTP_PORT', 587)), timeout=10) as sm:
            sm.starttls()
            if os.environ.get('SMTP_USER'):
                sm.login(os.environ['SMTP_USER'], os.environ.get('SMTP_PASS', ''))
            sm.send_message(msg)
        record['sent'] = True
        print('[ALERT sent -> %s] %s' % (destination, body))
    except Exception as e:
        record['error'] = str(e)
        print('[ALERT failed]', e)


# ---------------------------------------------------------------- http

class Handler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'

    def log_message(self, *a):
        pass                                   # keep the console readable

    def _json(self, code, obj):
        raw = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(raw)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(raw)

    def _body(self):
        n = int(self.headers.get('Content-Length') or 0)
        return json.loads(self.rfile.read(n) or b'{}')

    def do_GET(self):
        if self.path in ('/', '/index.html'):
            return self._file('index.html', 'text/html; charset=utf-8')
        if self.path == '/api/data':
            with _lock:
                return self._json(200, {
                    'now': time.time(),
                    'window': BUFFER_SECONDS,
                    'box_on': latest['box_on'],
                    'a': latest['a'], 'b': latest['b'],
                    'history': {s: list(history[s]) for s in ('a', 'b')},
                    'settings': settings,
                    'alerts': list(alert_log),
                    'sim': sensors.sim_state(),
                })
        if self.path == '/api/settings':
            with _lock:
                return self._json(200, settings)
        return self._json(404, {'error': 'not found'})

    def do_POST(self):
        try:
            body = self._body()
        except Exception:
            return self._json(400, {'error': 'bad json'})

        if self.path == '/api/display':          # remote button toggle
            s = body.get('sensor')
            if s not in ('a', 'b') or not isinstance(body.get('on'), bool):
                return self._json(400, {'error': 'need sensor a|b and on true|false'})
            sensors.set_display(s, body['on'])
            with _lock:
                latest[s]['display_on'] = body['on']
                return self._json(200, {'sensor': s, 'display_on': latest[s]['display_on']})

        if self.path == '/api/settings':
            new = dict(settings)
            try:
                for k in ('min_c', 'max_c'):
                    if k in body:
                        new[k] = float(body[k])
                for k in ('msg_low', 'msg_high', 'destination'):
                    if k in body:
                        new[k] = str(body[k])
                if 'alerts_enabled' in body:
                    new['alerts_enabled'] = bool(body['alerts_enabled'])
                if 'cooldown_s' in body:
                    new['cooldown_s'] = max(0, int(body['cooldown_s']))
            except (TypeError, ValueError):
                return self._json(400, {'error': 'bad field type'})
            if not (VALID_MIN_C <= new['min_c'] <= VALID_MAX_C) or \
               not (VALID_MIN_C <= new['max_c'] <= VALID_MAX_C):
                return self._json(400, {'error': 'limits must be within -10..63 C'})
            if new['min_c'] >= new['max_c']:
                return self._json(400, {'error': 'min must be below max'})
            with _lock:
                settings.clear()
                settings.update(new)
                save_settings(settings)
                return self._json(200, settings)

        if self.path == '/api/sim':              # demo controls, simulator only
            sensors.sim_set(box_on=body.get('box_on'),
                            a_plugged=body.get('a_plugged'),
                            b_plugged=body.get('b_plugged'))
            return self._json(200, sensors.sim_state())

        return self._json(404, {'error': 'not found'})

    def _file(self, name, ctype):
        path = os.path.join(HERE, 'static', name)
        try:
            with open(path, 'rb') as f:
                raw = f.read()
        except FileNotFoundError:
            return self._json(404, {'error': 'static/%s not created yet' % name})
        self.send_response(200)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


class Server(ThreadingHTTPServer):
    # Dual-stack. On Windows 'localhost' resolves to ::1 first; an IPv4-only
    # socket makes every request eat a ~2 s connect timeout, which blows the
    # "control response under 1 second" requirement. Listening on both fixes it.
    address_family = socket.AF_INET6
    daemon_threads = True

    def server_bind(self):
        self.socket.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 0)
        ThreadingHTTPServer.server_bind(self)


if __name__ == '__main__':
    threading.Thread(target=sample_loop, daemon=True).start()
    print('serving http://localhost:%d  (Ctrl-C to stop)' % PORT)
    Server(('::', PORT), Handler).serve_forever()
