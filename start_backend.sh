#!/bin/bash
# IMK Backend Startup Script

set -e

cd "$(dirname "$0")/backend"

# Activate virtual environment
source ../.venv/bin/activate

# Check if backend is already running
if pgrep -f "uvicorn.*app.main:app" > /dev/null; then
    echo "❌ Backend is already running!"
    echo "   Stop it first with: pkill -f 'uvicorn.*app.main:app'"
    exit 1
fi

# Clean up any orphaned processes
echo "🧹 Running cleanup..."
python3 ../cleanup.py

echo ""
echo "🚀 Starting IMK Backend..."
echo "   Host: 0.0.0.0"
echo "   Port: 8000"
echo "   Workers: 1"
echo ""
echo "   Admin Panel: http://localhost:8000/admin/"
echo "   API Docs: http://localhost:8000/docs"
echo ""

# Start the backend
uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 1
