# IMK Quick Reference Guide

## Starting the Backend

```bash
cd /home/ubuntu/imk
./start_backend.sh
```

## Stopping the Backend

```bash
# Graceful stop (Ctrl+C in terminal where backend is running)
# OR
pkill -f "uvicorn.*app.main:app"
```

## Cleanup Orphaned Processes

```bash
cd /home/ubuntu/imk
python3 cleanup.py
```

## Check System Status

```bash
# Running matches
curl -s http://localhost:8000/api/stream/live | python3 -m json.tool

# Backend health
curl -s http://localhost:8000/docs

# Orphaned processes
ps aux | grep mupen64plus | grep -v grep
ps aux | grep Xvfb | grep -v grep

# System resources
free -h
```

## Common Operations

### Create and Start Match (via Admin)
1. Go to: http://your-server:8000/admin/
2. Login (password: admin)
3. Click "Matches" → "New Match"
4. Fill in details, click "Create"
5. Click "Start Match"
6. Go to "Viewer" to watch

### Stop All Matches
```bash
curl -X POST http://localhost:8000/admin/cleanup -H "Cookie: imk_admin=admin"
```

### View Logs
```bash
tail -f backend/logs/backend.log
```

## Troubleshooting

### Backend won't start
```bash
# Check if already running
ps aux | grep uvicorn

# Force kill and cleanup
pkill -9 -f uvicorn
python3 cleanup.py

# Try again
./start_backend.sh
```

### Video not showing
1. Stop current match
2. Start new match (after our fixes)
3. Check browser console for errors
4. Verify WebSocket connection

### Too many matches running
```bash
# Check count
curl -s http://localhost:8000/api/stream/live | python3 -c "import sys, json; print(len(json.load(sys.stdin)))"

# Your instance can handle: 5-8 matches max
# For 30 matches, you need c7a.8xlarge (32 cores)
```

## File Locations

- **Backend code:** `/home/ubuntu/imk/backend/`
- **Logs:** `/home/ubuntu/imk/backend/logs/backend.log`
- **Savestates:** `/home/ubuntu/imk/training/data/savestates/`
- **Config:** `/home/ubuntu/imk/.m64p/instances/`
- **Cleanup script:** `/home/ubuntu/imk/cleanup.py`
- **Start script:** `/home/ubuntu/imk/start_backend.sh`

## URLs

- **Admin Panel:** http://localhost:8000/admin/
- **API Docs:** http://localhost:8000/docs
- **Viewer:** http://localhost:8000/admin/viewer
- **Health Check:** http://localhost:8000/api/stream/health

## Performance Limits

**Your Instance (8 cores, 30GB RAM):**
- Conservative: 5-6 matches
- Optimal: 6-7 matches
- Maximum: 8 matches

**For 30 matches, upgrade to:**
- c7a.8xlarge (32 cores, 64GB RAM)
- OR 4-5 instances with load balancer

## Important Notes

✅ Always use `./start_backend.sh` - it runs cleanup automatically
✅ Stop matches via admin panel or API, not by killing processes
✅ The backend now cleans up automatically on startup/shutdown
✅ No more orphaned processes!
✅ Production-ready with systemd service

## Emergency Reset

```bash
# Nuclear option - reset everything
pkill -9 -f uvicorn
pkill -9 -f mupen64plus
pkill -9 -f run_bridge_server
pkill -9 -f Xvfb
python3 cleanup.py
./start_backend.sh
```
