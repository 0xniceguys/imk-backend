# Production Deployment Guide

**IMK Backend - Production Configuration**
**Date:** March 3, 2026
**Status:** ✅ Deployed and Tested

---

## Overview

The IMK backend is now running in a **production-ready configuration** with:

- **4 Uvicorn workers** for load balancing and crash resilience
- **Nginx reverse proxy** with upstream health checking
- **Systemd service management** with auto-restart
- **Health check endpoints** for monitoring
- **Fresh database** with clean schema

---

## Architecture

### Multi-Worker Setup

```
Internet (HTTPS)
    ↓
Nginx (Port 443/80)
    ↓ (Reverse Proxy)
Uvicorn (Port 8000)
    ├─ Worker 1
    ├─ Worker 2
    ├─ Worker 3
    └─ Worker 4
    ↓
PostgreSQL (Port 5432)
```

**Benefits:**
- **Load Balancing:** Nginx + Uvicorn distribute requests across 4 workers
- **Crash Resilience:** If one worker crashes, other 3 continue serving requests
- **Zero Downtime:** Graceful restarts reload workers one at a time
- **Scalability:** Can add more workers (up to 2x CPU cores)

---

## Configuration Files

### 1. Systemd Service: `/etc/systemd/system/imk.service`

```ini
[Unit]
Description=IMK FastAPI Backend with Multiple Workers
After=network.target postgresql.service

[Service]
Type=simple
User=ubuntu
WorkingDirectory=/home/ubuntu/imk/backend
EnvironmentFile=/home/ubuntu/imk/backend/.env

# 4 workers (half of 8 CPU cores)
ExecStart=/home/ubuntu/imk/.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 4

# Graceful shutdown with 30s timeout
TimeoutStopSec=30
KillMode=mixed
KillSignal=SIGTERM

# Auto-restart on failure
Restart=always
RestartSec=5

# Resource limits
MemoryMax=24G
TasksMax=1024

# Logging
StandardOutput=journal
StandardError=journal
SyslogIdentifier=imk-backend

# Security hardening
NoNewPrivileges=true
PrivateTmp=true

[Install]
WantedBy=multi-user.target
```

**Key Features:**
- `--workers 4`: Spawns 4 independent worker processes
- `Restart=always`: Auto-restart on crashes
- `MemoryMax=24G`: Prevents runaway memory usage (6GB/worker)
- `TimeoutStopSec=30`: Allows 30s for graceful shutdown

### 2. Nginx Configuration: `/etc/nginx/sites-available/imk.conf`

```nginx
upstream imk_backend {
    server 127.0.0.1:8000 max_fails=3 fail_timeout=30s;
    keepalive 64;
}

server {
    server_name immortalkombat.mercle.ai;

    client_max_body_size 50M;
    client_body_buffer_size 256k;

    # API endpoints
    location /api/ {
        proxy_pass http://imk_backend;
        proxy_http_version 1.1;
        proxy_set_header Connection "";
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 300s;
        proxy_buffering off;
    }

    # WebSocket endpoints
    location /ws/ {
        proxy_pass http://imk_backend;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_read_timeout 3600s;
        proxy_send_timeout 3600s;
        proxy_buffering off;
    }

    # Health check
    location /health {
        proxy_pass http://imk_backend;
        proxy_read_timeout 5s;
        access_log off;
    }

    # SSL Configuration (Certbot)
    listen 443 ssl http2;
    ssl_certificate /etc/letsencrypt/live/immortalkombat.mercle.ai/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/immortalkombat.mercle.ai/privkey.pem;

    # Security headers
    add_header X-Frame-Options "SAMEORIGIN" always;
    add_header X-Content-Type-Options "nosniff" always;
    add_header X-XSS-Protection "1; mode=block" always;
}
```

**Key Features:**
- `upstream` block: Defines backend with health checking
- `keepalive 64`: Connection pooling for performance
- `max_fails=3 fail_timeout=30s`: Auto-remove unhealthy backend
- `proxy_buffering off`: Real-time streaming support
- Separate WebSocket handling with long timeouts

---

## Health Check Endpoints

### 1. Basic Health Check: `/health`

**Used by:** Load balancers, monitoring tools

```bash
curl https://immortalkombat.mercle.ai/health
```

**Response:**
```json
{"status": "ok"}
```

### 2. Detailed Health Check: `/health/detailed`

**Used by:** Ops dashboards, debugging

```bash
curl https://immortalkombat.mercle.ai/health/detailed
```

**Response:**
```json
{
  "status": "ok",
  "database": {
    "status": "connected"
  },
  "runners": {
    "count": 0,
    "matches": []
  },
  "system": {
    "cpu_percent": 1.3,
    "memory_percent": 9.0
  }
}
```

**Status Values:**
- `ok`: All systems operational
- `degraded`: Some subsystems failing (e.g., DB down)

---

## Operational Commands

### Service Management

```bash
# Start service
sudo systemctl start imk.service

# Stop service (graceful, waits 30s)
sudo systemctl stop imk.service

# Restart service (zero-downtime reload)
sudo systemctl restart imk.service

# Check status
sudo systemctl status imk.service

# Enable auto-start on boot
sudo systemctl enable imk.service

# View logs (live)
journalctl -u imk.service -f

# View logs (last 100 lines)
journalctl -u imk.service -n 100
```

### Nginx Management

```bash
# Test configuration
sudo nginx -t

# Reload configuration (zero downtime)
sudo nginx -s reload

# Restart Nginx
sudo systemctl restart nginx

# View access logs
tail -f /var/log/nginx/access.log

# View error logs
tail -f /var/log/nginx/error.log
```

### Database Management

```bash
# Connect to PostgreSQL
sudo -u postgres psql -d imkdb

# Check tables
sudo -u postgres psql -d imkdb -c "\dt"

# Create tables from models (if needed)
cd /home/ubuntu/imk/backend
/home/ubuntu/imk/.venv/bin/python -c "
import asyncio
from app.db.engine import engine
from app.db.base import Base
from app.db import models

async def create_tables():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    print('✅ Tables created')

asyncio.run(create_tables())
"

# Drop all data (DESTRUCTIVE!)
sudo -u postgres psql -d imkdb -c "
DROP SCHEMA public CASCADE;
CREATE SCHEMA public;
GRANT ALL ON SCHEMA public TO imk;
GRANT ALL ON SCHEMA public TO public;
"
```

---

## Monitoring

### Check Worker Processes

```bash
# Count active workers (should be 5: 4 workers + 1 resource tracker)
ps aux | grep "python.*multiprocessing" | grep -v grep | wc -l

# View worker process tree
pstree -p $(systemctl show -p MainPID imk.service | cut -d= -f2)
```

### Check System Resources

```bash
# CPU and memory usage
ps aux | grep "python.*uvicorn" | grep -v grep

# Detailed resource breakdown
systemctl status imk.service
```

### Check Connectivity

```bash
# Internal health check
curl http://127.0.0.1:8000/health

# External health check (through Nginx)
curl https://immortalkombat.mercle.ai/health

# Test API endpoints
curl https://immortalkombat.mercle.ai/api/fighters/
curl https://immortalkombat.mercle.ai/api/matches/

# Test admin panel
curl -L https://immortalkombat.mercle.ai/admin/fighters
```

---

## Crash Recovery

### Automatic Recovery

The systemd service is configured with `Restart=always`:
- **Worker crash:** Uvicorn automatically spawns replacement worker
- **Master process crash:** Systemd restarts entire service after 5s
- **System reboot:** Service auto-starts via `systemctl enable`

### Manual Recovery

If service is stuck:

```bash
# Force kill and restart
sudo systemctl stop imk.service
sudo pkill -9 -f "uvicorn app.main"
sudo systemctl start imk.service

# Verify recovery
sudo systemctl status imk.service
curl https://immortalkombat.mercle.ai/health
```

---

## Deployment Workflow

### Deploying Code Changes

```bash
# 1. Pull latest code
cd /home/ubuntu/imk/backend
git pull origin main

# 2. Install dependencies (if changed)
/home/ubuntu/imk/.venv/bin/pip install -e .

# 3. Run database migrations (if needed)
DATABASE_URL="postgresql+asyncpg://imk:Imk2026Secure@127.0.0.1/imkdb" \
  /home/ubuntu/imk/.venv/bin/alembic upgrade head

# 4. Restart service (graceful, zero downtime)
sudo systemctl restart imk.service

# 5. Verify deployment
sleep 5
curl https://immortalkombat.mercle.ai/health
journalctl -u imk.service -n 50
```

### Rolling Back

```bash
# 1. Revert code
cd /home/ubuntu/imk/backend
git reset --hard <previous-commit-hash>

# 2. Restart service
sudo systemctl restart imk.service

# 3. Verify
curl https://immortalkombat.mercle.ai/health
```

---

## Performance Tuning

### Current Capacity

Based on `STREAMING_CAPACITY_ANALYSIS.md`:

- **Concurrent matches:** 4 (one per worker)
- **WebSocket viewers:** ~40 per 1Gbps NIC (network-bound)
- **CPU usage:** <10% during matches
- **Memory usage:** ~500MB per worker, ~2GB total

### Scaling Up

#### Add More Workers

Edit `/etc/systemd/system/imk.service`:

```ini
# Change from --workers 4 to --workers 6
ExecStart=/home/ubuntu/imk/.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 6
```

Then reload:

```bash
sudo systemctl daemon-reload
sudo systemctl restart imk.service
```

**Rule of thumb:** `workers = (2 x CPU cores) + 1` for I/O-bound apps

#### Optimize Streaming

For 100+ viewers, implement CDN + HLS:

1. **Record matches to HLS segments** (already in `/home/ubuntu/imk/hls_output`)
2. **Serve via CDN** (CloudFlare, Fastly, etc.)
3. **Use adaptive bitrate** (HLS auto-selects quality)

---

## Troubleshooting

### Service won't start

```bash
# Check logs
journalctl -u imk.service -n 100

# Common issues:
# - Port 8000 already in use
sudo lsof -i :8000
sudo pkill -f uvicorn

# - Database connection failed
sudo -u postgres psql -c "\l" | grep imkdb
sudo -u postgres psql -c "ALTER USER imk WITH PASSWORD 'Imk2026Secure';"

# - .env file missing
cat /home/ubuntu/imk/backend/.env
```

### High memory usage

```bash
# Check per-worker memory
ps aux | grep "python.*multiprocessing" | awk '{sum+=$6} END {print sum/1024 "MB"}'

# Reduce workers if needed
# Edit /etc/systemd/system/imk.service and lower --workers value
```

### Slow response times

```bash
# Check if backend is overloaded
curl https://immortalkombat.mercle.ai/health/detailed

# Check Nginx access logs for slow requests
tail -f /var/log/nginx/access.log | grep "request_time"

# Check database slow queries
sudo -u postgres psql -d imkdb -c "
SELECT pid, now() - query_start as duration, query
FROM pg_stat_activity
WHERE state = 'active'
ORDER BY duration DESC;
"
```

### WebSocket connections dropping

```bash
# Check Nginx WebSocket proxy timeouts
cat /etc/nginx/sites-available/imk.conf | grep "ws/"

# Increase timeouts if needed (currently 3600s = 1 hour)
# Edit /etc/nginx/sites-available/imk.conf
# Then: sudo nginx -s reload
```

---

## Security Notes

### Current Security Measures

1. **SSL/TLS:** Managed by Let's Encrypt (auto-renewal via Certbot)
2. **Security headers:** X-Frame-Options, X-Content-Type-Options, X-XSS-Protection
3. **NoNewPrivileges:** Systemd prevents privilege escalation
4. **PrivateTmp:** Systemd isolates /tmp directory
5. **Memory limits:** Prevents DoS via memory exhaustion

### Recommended Additions

1. **Rate limiting** (Nginx):
   ```nginx
   limit_req_zone $binary_remote_addr zone=api:10m rate=10r/s;
   limit_req zone=api burst=20 nodelay;
   ```

2. **Firewall rules** (ufw):
   ```bash
   sudo ufw allow 80/tcp
   sudo ufw allow 443/tcp
   sudo ufw enable
   ```

3. **Database backups** (cron):
   ```bash
   # Add to crontab -e
   0 2 * * * pg_dump -U imk imkdb | gzip > /backup/imkdb_$(date +\%Y\%m\%d).sql.gz
   ```

---

## Testing Checklist

After any deployment:

- [ ] Service is running: `systemctl status imk.service`
- [ ] 4 workers active: `ps aux | grep multiprocessing | wc -l` (should be 5)
- [ ] Health check passes: `curl https://immortalkombat.mercle.ai/health`
- [ ] API endpoints work: `curl https://immortalkombat.mercle.ai/api/fighters/`
- [ ] Admin panel loads: `curl -L https://immortalkombat.mercle.ai/admin/fighters`
- [ ] Database connected: Check `/health/detailed`
- [ ] No errors in logs: `journalctl -u imk.service -n 100`
- [ ] Nginx no errors: `tail -100 /var/log/nginx/error.log`

---

## Current Status (March 3, 2026)

✅ **All systems operational**

```
Service Status:    ✅ Active (running)
Workers:           ✅ 4 workers + 1 tracker = 5 processes
Health Check:      ✅ https://immortalkombat.mercle.ai/health → 200 OK
Database:          ✅ Connected (9 tables)
API Endpoints:     ✅ All responding
Admin Panel:       ✅ Loading (requires auth)
CPU Usage:         ✅ 1-2%
Memory Usage:      ✅ 9% (360MB/worker)
Nginx:             ✅ Active
SSL Certificate:   ✅ Valid (Let's Encrypt)
```

**Database:** Fresh schema, all data dropped (pre-production testing)

---

## Contact

For production issues, check:
1. Service logs: `journalctl -u imk.service -f`
2. Nginx logs: `tail -f /var/log/nginx/error.log`
3. Health endpoint: `https://immortalkombat.mercle.ai/health/detailed`
