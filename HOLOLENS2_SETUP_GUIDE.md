# HINT Study Console — Microsoft HoloLens 2 Setup Guide

This version of the HINT Study Console connects to Microsoft HoloLens 2 through
**HL2SS** and validates two live streams:

1. **PV/front RGB camera**
2. **Extended Eye Tracking (EET)** — combined, left-eye, and right-eye gaze rays

The connection is intentionally separated from the validation window. The
streams remain connected when the validation window is closed.

## A. One-time setup on the study PC

1. Install/update the HINT Python environment:

   ```powershell
   pip install -r requirements.txt
   ```

   This installs the additional HL2SS desktop dependencies (`opencv-python`,
   `av`, and `requests`).

2. Download/extract the current HL2SS repository from:

   `https://github.com/jdibenes/hl2ss`

3. Keep the repository somewhere stable on the study PC, for example:

   `D:\ResearchTools\hl2ss\`

4. In the HINT console you may select either:

   - `D:\ResearchTools\hl2ss\`, or
   - `D:\ResearchTools\hl2ss\viewer\`

   The selected location is valid when the console can find `hl2ss.py` and
   `hl2ss_lnm.py` in the `viewer` directory.

## B. One-time setup on HoloLens 2

1. Update HoloLens 2.
2. Open **Settings → Update & Security → For developers**.
3. Enable **Developer Mode**.
4. Enable **Device Portal**.
5. Find the headset IPv4 address using one of these methods:
   - **Settings → Network & Internet → Wi-Fi → Advanced options / Hardware properties**, or
   - say **“What's my IP address?”** on HoloLens.
6. On the study PC, you can verify Device Portal by opening:

   `https://<HOLOLENS-IP>`

   The browser may warn about the HoloLens self-signed certificate.
7. Download the current HL2SS `.appxbundle` from the HL2SS Releases page.
8. Install it either directly on HoloLens or through **Device Portal → Views → Apps**.
9. After installation, confirm **hl2ss** appears under **All apps** on HoloLens.

### Research Mode

The current HINT integration uses only the PV/front camera and Extended Eye
Tracking. Current HL2SS releases can use these without Research Mode. Research
Mode is only needed if you later extend the console to raw Research Mode VLC,
depth, or IMU streams. Enabling Research Mode also increases battery usage.

## C. Before every participant

1. Put HoloLens 2 on the participant.
2. Run **Settings → System → Calibration → Eye Calibration → Run eye calibration**.
3. Confirm calibration completes successfully.
4. Launch the **hl2ss** app on HoloLens.
5. On first use, approve the requested permissions, especially:
   - Camera
   - Eye tracker
   - Microphone
   - User movements
6. Keep the `hl2ss` app running.
7. Close any standalone HL2SS PV/EET viewer scripts on the PC. HL2SS supports
   multiple different streams at once, but only one client for a given stream.
8. Keep the HoloLens and PC on the same reachable network. Avoid guest Wi-Fi
   configurations that isolate devices from each other.

## D. Connect from the HINT Study Console

1. Run:

   ```powershell
   python main.py
   ```

2. Open **Devices**.
3. In **Microsoft HoloLens 2 — Eye Gaze + PV Camera**:
   - enter the HoloLens IPv4 address;
   - select the downloaded HL2SS repository root or `viewer` folder;
   - select Extended Eye Tracking rate (`30`, `60`, or `90 Hz`);
   - leave the recommended PV mode at `1280 × 720 @ 30 FPS` unless bandwidth is limited.
4. Click **Connect HoloLens**.
5. Watch the connection log and progress indicator.
6. A connection is treated as successful only after **both** streams produce
   live packets:
   - PV camera frame received;
   - Extended Eye Tracking packet received.
7. The HoloLens device status then becomes:

   **Connected and receiving live PV camera + eye-gaze data**

## E. Automatic validation after connection

Immediately after the first successful connection, the console automatically
opens **HoloLens 2 Connection Validation** exactly once for that connection.

Verify all of the following:

- the RGB image updates when the wearer/headset moves;
- **Eye calibration = VALID**;
- combined gaze is valid and its direction changes when the participant looks around;
- left/right eye rays update when available;
- last camera-frame age remains small;
- last eye-packet age remains small.

You may then close the validation window. Closing it **does not disconnect** the
HoloLens and **does not stop** either live stream.

## F. Validate again during the study

At any later time while HoloLens is connected:

1. Go to **Devices → Microsoft HoloLens 2**.
2. Click **Validate Connection**.
3. The console first checks whether recent PV and EET packets are still arriving.
4. If healthy, it reopens the same validation window.
5. If unhealthy, it shows a warning instead; reconnect before continuing data
   collection.

## G. What the eye-gaze values mean

The validation window displays each gaze ray as:

- `valid`
- `origin (x, y, z)`
- `direction (x, y, z)`

for:

- combined gaze;
- left eye;
- right eye.

The console does **not** draw a gaze cursor directly over the PV image in this
version. A scientifically valid camera overlay requires synchronizing the EET
and PV timestamps and transforming the gaze ray into the PV camera coordinate
system. An arbitrary 2-D mapping would look convincing but could be wrong.

## H. Troubleshooting

### “HL2SS client folder is invalid”

Select the downloaded HL2SS repository root or its `viewer` directory. The
console must find both `viewer/hl2ss.py` and `viewer/hl2ss_lnm.py`.

### Import error mentioning `av`, `cv2`, or `requests`

Run:

```powershell
pip install -r requirements.txt
```

using the same Python environment that launches `main.py`.

### Connection refused / timed out

Check:

- correct HoloLens IP;
- PC and HoloLens on the same reachable LAN/Wi-Fi;
- `hl2ss` app is currently open on HoloLens;
- Windows/network firewall rules;
- no network client isolation on the access point.

### Camera works but eye gaze does not

Check:

- eye-tracker permission for the HL2SS app;
- participant eye calibration;
- HoloLens is worn correctly;
- another program is not already using the EET stream;
- reconnect after fixing permissions/calibration.

### Eye data arrives but calibration is NOT VALID

The network connection is working, but the data should not be treated as valid
participant gaze. Run:

**Settings → System → Calibration → Eye Calibration → Run eye calibration**

then validate again.

### Eye gaze works but camera does not

Check camera permission, close any other HL2SS PV client, and reconnect. If the
network is constrained, select the lower-bandwidth `760 × 428 @ 30 FPS` mode.

### Stream stops during a participant session

Click **Validate Connection**. If either stream is stale, disconnect and reconnect
HoloLens before continuing the affected condition. Do not assume a green Wi-Fi
icon means application-level sensor packets are still reaching HINT.
