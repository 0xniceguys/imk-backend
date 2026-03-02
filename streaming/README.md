# Streaming (planned)

This folder will contain the web implementation and server-side control plane.

Current status:
- placeholder only (training is the priority)

Planned components:
- `api/`: process/session control (start, stop, load state, send inputs)
- `web/`: browser UI for stream + controls
- `media/`: video/audio pipeline (likely WebRTC later)

## Interface contract to keep in mind

`streaming/` should call into the same emulator/runtime layer used by training, instead of duplicating process logic.

For now, training uses the root shell launcher scripts directly. Later we can move that into a shared `runtime/` package.
