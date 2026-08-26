# HoloLens 2 Extended Eye Tracking timeout troubleshooting

If the console reports `Extended Eye Tracking stream error: timed out`, use this order.

## 1. Use HINT Console v1.1.1 or later
v1.1 used a 3-second EET socket timeout. v1.1.1 removes that short timeout and follows the official HL2SS EET sample behavior.

## 2. Confirm the hl2ss app is actually running on the HoloLens
Open **All apps -> hl2ss** on HoloLens 2 and leave it running while connecting from the PC.

## 3. Verify permissions for hl2ss.exe
On HoloLens, check Settings -> Privacy and make sure hl2ss.exe is allowed for:
- Eye tracker
- User movements
- Camera
- Microphone

The EET stream particularly depends on eye-tracker access. If you previously denied access, the first-run prompt will not necessarily appear again; re-enable the permission in Settings.

## 4. Run eye calibration
Settings -> System -> Calibration -> Eye Calibration -> Run eye calibration.

Calibration validity and stream availability are different concepts, but calibration should be completed before study data collection.

## 5. Test HL2SS EET outside the HINT Console
In the exact HL2SS `viewer` folder selected in the console:
1. Open `client_stream_eet.py`.
2. Set `host = '<your HoloLens IPv4 address>'`.
3. Run:
   `python client_stream_eet.py`

Expected behavior: the terminal continuously prints calibration and combined/left/right eye-gaze data.

- If this official sample also hangs/fails, the issue is HoloLens/HL2SS/permissions/network, not the HINT GUI.
- If the official sample works but the HINT console fails, use the same `viewer` folder in HINT and make sure no other EET client is still running.

## 6. Do not run two EET clients simultaneously
HL2SS supports multiple different streams, but only one client per stream. Close `client_stream_eet.py` before clicking Connect HoloLens in HINT.

## 7. Keep server and Python viewer versions aligned
Use the appxbundle and `viewer` Python files from the same HL2SS release/tag whenever possible.

## 8. Network check
Both devices should be on the same reachable LAN/Wi-Fi. Corporate/guest Wi-Fi can block device-to-device traffic even when both devices have Internet access.

Windows PowerShell quick checks:

`ping <HOLOLENS_IP>`

`Test-NetConnection <HOLOLENS_IP> -Port 3817`

Port 3817 is the HL2SS Extended Eye Tracker stream port.

## Interpretation
- PV camera works, EET fails: prioritize Eye tracker permission, User movements permission, calibration, EET port 3817, and another EET client already occupying the stream.
- Both PV and EET fail: prioritize wrong IP, hl2ss app not running, Wi-Fi isolation/firewall, or incompatible/mismatched HL2SS installation.
