# Study 3 Start-Screen Fix — v1.8.1

## Problem

After **Prepare Selected Video**, the participant display could become fully
black without showing **Ready to begin?** or **START ACTIVITY**. On Windows,
`QVideoWidget` can use a native video surface that remains above ordinary Qt
child widgets, so the stopped black video surface covered the Start overlay.

## Fix

- Hide the native video widget throughout Study 3 preparation.
- Present the participant Start page while the video widget is hidden.
- Show the video widget only after participant Start is accepted and immediately
  before playback begins.
- Resize the Start page with the fullscreen participant window.
- Preserve retry/error behavior: a failed activity start leaves the Start page
  visible and keeps the video surface hidden.

## Verification

Regression tests cover both the prepared/waiting state and the accepted Start
transition.
