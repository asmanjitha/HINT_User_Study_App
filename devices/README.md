# devices/

Device adapters for the HINT Study Console.

## Shimmer (real hardware integration in V0.7)

`shimmer_device.py` connects to a paired classic Shimmer3 over its Windows
Bluetooth virtual COM/RFCOMM port using `pyserial`. It configures the study
channels (GSR + optical PPG on internal ADC A13), enables the expansion power
needed by the optical probe, requests 128 Hz streaming, verifies the enabled
channels with an inquiry response, and writes all received samples to a CSV.

The serial reader runs in a background Python thread so a continuous Shimmer
stream cannot block the Qt GUI. `shimmer_protocol.py` contains the binary
command/framing helpers and is intentionally independent of Qt/pyserial so it
can be unit-tested without hardware.

Realtime connection logs are written under:

```
data/device_logs/shimmer/shimmer_stream_YYYYMMDD_HHMMSS_COMx.csv
```

The Devices page also exposes **Check Live Data**, which compares the Shimmer
sample counter across a 1.5-second window and checks the age of the latest
packet. This verifies that data is actually reaching the HINT application,
not merely that the operating system still considers the COM port open.

## Keyboard, joystick, and microphone (real hardware integration in v1.0)

`input_devices.py` adds selectable real-device adapters:

- Windows keyboards are enumerated with Raw Input device identities; one or two may be bound.
- One joystick/gamepad may be initialized and polled through pygame/SDL.
- One microphone may be opened through sounddevice/PortAudio; live callbacks confirm data reception.

## HoloLens 2 (real HL2SS integration in v1.1)

`hololens_device.py` connects to the HL2SS server running on Microsoft HoloLens 2.
It keeps the PV/front RGB camera stream and Extended Eye Tracking stream alive in
background threads and exposes current frames, gaze rays, calibration validity,
stream counts, packet ages, and connection-health checks through `DeviceManager`.

The GUI imports the official HL2SS Python client from a user-selected repository
root or `viewer` folder rather than vendoring third-party HL2SS source code.

