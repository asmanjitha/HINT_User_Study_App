# Shimmer GUI Integration — V0.7

## Added

- Real classic-Shimmer3 Bluetooth/serial adapter using `pyserial`.
- Dedicated guided Shimmer setup panel in **Devices**.
- Serial-port discovery with Bluetooth candidates prioritized.
- Step-by-step connection progress bar and connection log.
- Hardware and firmware identification before streaming.
- Automatic HINT sensor configuration:
  - GSR
  - optical PPG via internal ADC A13
  - 128 Hz sampling
  - GSR auto-range
  - internal/3 V expansion power enabled for the optical pulse probe
- Inquiry verification that both GSR and PPG channels are actually enabled.
- Background realtime stream parser so the GUI remains responsive.
- Green `Receiving Data` confirmation only after the first valid sensor packet
  reaches the application.
- Explicit success dialog after the first valid packet confirms realtime data
  is reaching the GUI and shows the CSV path.
- Continuous realtime CSV recording under `data/device_logs/shimmer/`.
- Live values/status display: sample count, rate, last-packet age, GSR raw/ADC/
  range, PPG raw, hardware/firmware, and CSV path.
- **Check Live Data** button: measures whether the sample counter increases over
  1.5 seconds and whether the last packet is fresh.
- Automatic Warning status if an established stream stops delivering samples
  for more than 2 seconds; status recovers automatically when samples resume.
- Explicitly rejects Shimmer3R in this classic-Shimmer3 virtual-COM adapter,
  preventing an incompatible sensor bitmap from being applied silently.
- Graceful error message when `pyserial` is missing.
- Shimmer is safely disconnected during application shutdown.

## Files

- `devices/shimmer_protocol.py` — binary protocol helpers/parser.
- `devices/shimmer_device.py` — serial connection, streaming thread, CSV logger.
- `devices/device_manager.py` — uses the real Shimmer adapter and forwards live
  Shimmer signals to the GUI.
- `gui/devices_page.py` — guided setup/progress/live verification interface.
- `tests/test_shimmer_protocol.py` — hardware-independent protocol tests.
