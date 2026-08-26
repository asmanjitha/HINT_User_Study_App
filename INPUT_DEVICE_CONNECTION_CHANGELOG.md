# HINT Study Console v1.0 — Input Device Connection

## Added real selectable input-device panels

The Devices page now contains separate hardware-selection panels for:

- **Keyboard** — one required, maximum two physical keyboards.
- **Joystick / gamepad** — one selected device.
- **Microphone** — one selected input device.

Each panel provides a device drop-down, Refresh, Connect, Check Connection,
and Disconnect controls. Shimmer remains unchanged and HoloLens remains a
placeholder adapter.

## Keyboard

On Windows, keyboards are enumerated through the Win32 **Raw Input** device
list. The selected Raw Input device paths are kept as the physical identities
for the study, which is the required foundation for distinguishing two
keyboards later when routing/logging WM_INPUT events.

The console enforces:

- at least one selected keyboard;
- at most two selected keyboards;
- Keyboard 1 and Keyboard 2 cannot be the same device.

`Check Connection` re-enumerates the hardware and confirms that all selected
Raw Input device identities are still present.

## Joystick / gamepad

Joystick/gamepad enumeration and connection use `pygame`/SDL. The selected
joystick is initialized and polled every 250 ms while connected. `Check
Connection` performs a fresh poll and reports whether the open joystick is
still responding.

## Microphone

Microphones are enumerated through `python-sounddevice`/PortAudio, filtering
to devices with at least one input channel. Connecting opens a mono input-only
monitoring stream. The device is only promoted to **Receiving Data** after a
real audio callback reaches the application.

The Devices page also displays a live input-level meter. `Check Live Audio`
verifies that the stream is active and that recent audio callbacks are still
arriving.

## Dependencies

Added:

```text
pygame>=2.6.1
sounddevice>=0.5.1
```

Install/update with:

```bash
pip install -r requirements.txt
```

## Not yet added

This milestone establishes hardware enumeration, selection, connection, and
connection verification. It does **not yet** save keyboard/joystick/microphone
study data into the T##/R## folders. That recording/routing layer can now use
the exact selected device identities exposed by `DeviceManager`.
