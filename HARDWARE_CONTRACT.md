# Box <-> PC contract (Lab 1)

One endpoint, one direction, once a second. The box talks; the PC never initiates.

## 1. The box POSTs once a second

`POST http://<pc-ip>:8000/api/ingest`  `Content-Type: application/json`

```json
{
  "t_ms": 123456,
  "a": { "c": 24.31, "status": "ok",        "display_on": true  },
  "b": { "c": 31.02, "status": "unplugged", "display_on": false },
  "local_change": false
}
```

* `c` — degrees **Celsius, float**, 2 decimals. Units are decided here and nowhere else;
  the PC converts to °F for display. Valid range −10.0 to +63.0. Send `null` when
  `status` is not `ok`.
* `status` — `"ok"` or `"unplugged"`. Nothing else. Sensor faulted, open circuit,
  garbage reading, out of range -> `"unplugged"`.
* `display_on` — the sensor's local display state **as it actually is right now**
  (after the box applied whatever the PC last asked for, or after a local button press).
* `t_ms` — the box's own uptime in ms. Debug only; the PC timestamps by arrival, so
  **the box needs no RTC and no clock sync.**
* `local_change` — `true` only on the one POST right after somebody pressed a physical
  button. Tells the PC "adopt my `display_on` as the new desired state." Otherwise `false`.

## 2. The PC answers that same POST with the pending commands

```json
{ "display": { "a": true, "b": false } }
```

**Why the response and not a separate endpoint the box polls:** the box is already
opening one connection per second. Putting commands in the response costs zero extra
connections, zero extra radio wake-ups (it's battery powered), and needs no inbound
port or port-forward on the box. A second polling endpoint would double the traffic and
the battery drain to buy nothing. Worst-case command latency is one POST interval = 1 s,
which is exactly the requirement.

**Why "desired state" and not "toggle":** `display.a` is the state the PC wants, not an
edge. If a response is lost to a dropped packet, the next second re-sends the truth. No
command queue, no acknowledgements, no risk of a dropped or duplicated toggle leaving
the box out of sync. Idempotent is the whole trick.

## 3. "Box is off" = no POST for **5 seconds**

The master switch off, dead battery, and dead Wi-Fi all look identical, and they should:
the UI shows `no data available` for both sensors.

**Why 5 s:** at 1 Hz that tolerates 4 consecutive dropped POSTs, which covers a normal
Wi-Fi hiccup or a retransmit without flickering the display, while still reacting fast
enough that a grader flipping the switch sees the change almost immediately. The box
does **not** need to announce shutdown — silence is the signal, so a yanked battery
behaves the same as a clean power-off.

## 4. Not required, mention only if it comes up

* No auth. On a lab LAN this is fine. If it ever sits on the open internet, add one
  header `X-Box-Key: <shared string>` and nothing else.
* No retries, no buffering on the box. If a POST fails, drop it and send the next one a
  second later — the PC keeps the history, the box does not need to.
* HTTP, not MQTT/websockets. One POST/s from one device does not justify a broker.
