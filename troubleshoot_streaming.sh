#!/bin/bash
# Troubleshooting script for streaming issues

echo "🔍 IMK Streaming Diagnostics"
echo "=============================="
echo ""

echo "1. Checking backend process..."
if pgrep -f "uvicorn.*app.main:app" > /dev/null; then
    echo "   ✅ Backend is running"
    echo "   PID: $(pgrep -f 'uvicorn.*app.main:app')"
else
    echo "   ❌ Backend is NOT running!"
    echo "   Start it with: cd backend && source .venv/bin/activate && uvicorn app.main:app --host 0.0.0.0 --port 8000"
fi
echo ""

echo "2. Checking for active match runners..."
curl -s http://localhost:8000/api/stream/live 2>/dev/null | python3 -m json.tool 2>/dev/null
if [ ${PIPESTATUS[0]} -eq 0 ]; then
    echo "   ✅ API responding"
else
    echo "   ❌ API not responding"
fi
echo ""

echo "3. Checking for running emulators..."
if pgrep -f mupen64plus > /dev/null; then
    echo "   ✅ Emulator(s) running:"
    ps aux | grep mupen64plus | grep -v grep | awk '{print "      PID " $2 ": " $NF}'
else
    echo "   ⚠️  No emulators running (normal if no match started)"
fi
echo ""

echo "4. Checking for Xvfb displays..."
if pgrep Xvfb > /dev/null; then
    echo "   ✅ Xvfb running:"
    ps aux | grep Xvfb | grep -v grep | awk '{print "      " $0}'
else
    echo "   ⚠️  No Xvfb running (normal if no match started)"
fi
echo ""

echo "5. Checking for FFmpeg capture..."
if pgrep -f "ffmpeg.*x11grab" > /dev/null; then
    echo "   ✅ FFmpeg capturing:"
    ps aux | grep "ffmpeg.*x11grab" | grep -v grep | awk '{print "      PID " $2}'
else
    echo "   ⚠️  No FFmpeg capture (normal if no match started)"
fi
echo ""

echo "6. Required tools check..."
for tool in ffmpeg Xvfb xdotool xwininfo; do
    if command -v $tool &> /dev/null; then
        echo "   ✅ $tool installed"
    else
        echo "   ❌ $tool NOT installed!"
    fi
done
echo ""

echo "7. Vendor builds check..."
if [ -f "vendor/mupen64plus-core/projects/unix/libmupen64plus.so.2" ]; then
    echo "   ✅ Core library built"
else
    echo "   ❌ Core library missing! Run: cd vendor/mupen64plus-core/projects/unix && make all"
fi

if [ -f "vendor/mupen64plus-ui-console/projects/unix/mupen64plus" ]; then
    echo "   ✅ UI console built"
else
    echo "   ❌ UI console missing! Run: cd vendor/mupen64plus-ui-console/projects/unix && make all"
fi

if [ -f "vendor/n64train-input/n64train-input.so" ]; then
    echo "   ✅ Input plugin built"
else
    echo "   ❌ Input plugin missing! Run: cd vendor/n64train-input && make"
fi
echo ""

echo "=============================="
echo "📝 Quick Actions:"
echo ""
echo "To start the backend:"
echo "  cd backend && source .venv/bin/activate && uvicorn app.main:app --host 0.0.0.0 --port 8000"
echo ""
echo "To test match streaming:"
echo "  cd /home/ubuntu/imk && source .venv/bin/activate && python3 test_match_start.py"
echo ""
echo "To access admin panel:"
echo "  http://your-server:8000/admin/ (password: admin)"
echo ""
