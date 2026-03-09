#!/bin/bash
# Terminus Dev — Auto-connect phone + launch app
# Phone IP: 192.168.1.3 (set static IP in router to keep this permanent)

PHONE_IP="192.168.1.3"
PHONE_PORT="5555"

echo "📱 Connecting to phone at $PHONE_IP:$PHONE_PORT..."
adb connect "$PHONE_IP:$PHONE_PORT"

# Check it worked
if adb devices | grep -q "$PHONE_IP"; then
  echo "✅ Connected! Launching immortal kombat..."
  cd "$(dirname "$0")"
  flutter run
else
  echo "❌ Could not connect. Make sure your phone is on the same WiFi."
  echo "   If first time: plug in USB, run 'adb tcpip 5555', then unplug."
fi
