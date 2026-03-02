# Video Streaming Fix - March 2, 2025

## Problem
Video streaming was showing black frames with just an X cursor instead of actual game content.

## Root Causes Found

### 1. Wrong Data Directory Path
**Problem:** Code was pointing to `/usr/share/mupen64plus` which doesn't exist
**Solution:** Changed to `/usr/share/games/mupen64plus` (actual location on Ubuntu)
**File:** `backend/app/services/emulator.py` line 49

### 2. Wrong Video Plugin
**Problem:** Using `mupen64plus-video-rice.so` which requires OpenGL/GLX that Xvfb doesn't provide
**Solution:** Changed to `mupen64plus-video-glide64mk2.so` which works on headless displays
**File:** `backend/app/services/emulator.py` line 51

### 3. Backend Not Restarted
**Problem:** After fixing the code, the running uvicorn server still had old config loaded
**Solution:** Restarted backend to load new configuration

## How to Verify It's Working

### Test Script
Run this to capture a test frame:
```bash
cd /home/ubuntu/imk
source .venv/bin/activate
python3 test_match_start.py
```

If working, you'll see:
- Frame size: ~16KB (working) vs ~4KB (broken/black)
- P1/P2 health values
- "✅ SUCCESS! Video frames are being captured!"

### Check Configuration
```bash
source .venv/bin/activate
python3 -c "
import sys
sys.path.insert(0, 'backend')
from app.services.emulator import _DATA_DIR, _GFX_PLUGIN, IS_LINUX
print(f'Data dir: {_DATA_DIR}')
print(f'Video plugin: {_GFX_PLUGIN}')
"
```

Should show:
- Data dir: `/usr/share/games/mupen64plus` ✅
- Video plugin: `mupen64plus-video-glide64mk2.so` ✅

## Architecture Overview

The streaming system works as follows:

1. **Match Start** → Backend launches:
   - Xvfb virtual display (per match instance, e.g., `:107`)
   - Mupen64Plus emulator on that display
   - FFmpeg capturing at 15fps via x11grab
   - WebSocket server for broadcasting

2. **Frame Capture** → FFmpeg process:
   - Captures Xvfb display at 640x480
   - Encodes to MJPEG stream
   - Pipes to Python via stdout
   - Quality set to 20 (lower = higher quality)

3. **Frame Distribution**:
   - Backend receives JPEG frames (~16KB each)
   - Broadcasts via WebSocket to all viewers
   - Viewers render to HTML5 canvas

4. **Game State**:
   - Separate loop reads RAM via debugger
   - Broadcasts health, timer, positions as JSON
   - Runs at ~10Hz independently from video

## Files Modified

- `backend/app/services/emulator.py` - Fixed `_DATA_DIR` and `_GFX_PLUGIN`
- Window detection improved to handle Rice/GLideN64/Z64 window names

## Test Artifacts

- `/home/ubuntu/imk/test_match_start.py` - Test script to verify streaming
- `/home/ubuntu/imk/frame_final.jpg` - Working test capture (16KB, shows Kai vs Reptile)
- `/home/ubuntu/imk/troubleshoot_streaming.sh` - Diagnostic script

## Usage

### Start Match via Admin Panel
1. Go to: `http://your-server:8000/admin/matches`
2. Click "New Match"
3. Select agents, savestate, best-of
4. Click "Create Match"
5. Click "Start Match" on the match detail page

### View Stream
1. Go to: `http://your-server:8000/admin/viewer`
2. Select match from dropdown
3. Video should show actual gameplay at 16fps

### Important Notes
- **Always restart backend** after code changes: `pkill -f uvicorn`
- **Stop old matches** before starting new ones to avoid confusion
- **Check backend logs** if issues: `tail -f backend/logs/backend.log`
- Frame size indicates success: ~16KB = working, ~4KB = black screen

## Troubleshooting

Run the diagnostic script:
```bash
cd /home/ubuntu/imk
./troubleshoot_streaming.sh
```

Check for:
- Backend running
- Correct data dir configuration
- FFmpeg, Xvfb, xdotool installed
- Vendor components built (core, UI, input plugin)
