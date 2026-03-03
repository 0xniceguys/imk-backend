# IMK Stress Test Guide

## What It Does
Tests how many simultaneous matches your EC2 instance can safely handle without:
- Running out of memory (OOM)
- CPU saturation
- Performance degradation

## Safety Features ✅
- **Auto-stops at 85% RAM** - Prevents OOM kills
- **Auto-stops at 95% CPU** - Prevents system lockup
- **Gradual ramp-up** - Starts one match every 15 seconds
- **Real-time monitoring** - Shows resource usage after each match
- **Graceful cleanup** - Stops all matches when done
- **Hard limit: 12 matches** - Safety cap

## How to Run

```bash
cd /home/ubuntu/imk
source .venv/bin/activate
python3 stress_test_matches.py
```

## What You'll See

```
🔥 IMK MATCH STREAMING STRESS TEST
======================================================================
Started at: 2025-03-02 16:30:00

📊 Initial System Stats:
   CPU Cores: 8
   Total RAM: 30.0 GB
   Available RAM: 27.5 GB
   Used RAM: 2.5 GB (8.3%)

⚙️  Safety Settings:
   Max RAM: 85%
   Max CPU: 95% sustained
   Max Matches: 12
   Ramp-up delay: 15s between matches

🚀 Starting stress test...
----------------------------------------------------------------------

[Match  1] Starting...
[Match  1] ✓ Running
           RAM: 2.8/30.0 GB (9.3%)
           CPU: 12.5%
           Available: 27.2 GB
           Waiting 15s before next match...

[Match  2] Starting...
[Match  2] ✓ Running
           RAM: 3.1/30.0 GB (10.3%)
           CPU: 24.8%
           Available: 26.9 GB
           Waiting 15s before next match...

... continues until limit reached ...

======================================================================
📈 FINAL RESULTS
======================================================================
✅ Successfully running: 6 matches
   Match IDs: stress-test-01, stress-test-02, ...

📊 Final Resource Usage:
   RAM: 4.5/30.0 GB (15.0%)
   Available: 25.5 GB
   CPU: 75.5%

📉 Estimated Per-Match Usage:
   RAM: ~350 MB
   CPU: ~12.6%

💡 Recommendations:
   ✓ GOOD - This is a safe operating point
   Recommended: 6 simultaneous matches

🎯 CONCLUSION: Your instance can safely handle 6 simultaneous matches
```

## Customizing Settings

Edit `stress_test_matches.py` to adjust:

```python
MAX_RAM_PERCENT = 85  # Stop if RAM > this
MAX_CPU_PERCENT = 95  # Stop if CPU > this
MAX_MATCHES = 12      # Hard limit
RAMP_UP_DELAY = 15    # Seconds between matches
```

## Stopping Early

Press **Ctrl+C** to stop the test early. All matches will be cleaned up gracefully.

## Expected Results

Based on your instance (8 cores, 30GB RAM):

**Conservative estimate:** 5-6 matches
**Optimal:** 6-7 matches
**Maximum:** 8-10 matches (if you push it)

## What Happens After

The test will:
1. Run all matches for 30 seconds
2. Let you inspect them
3. Auto-cleanup and stop everything
4. Show final recommendations

## Interpreting Results

### RAM Usage
- **< 50%** = Can handle more matches
- **50-70%** = Good operating point
- **> 70%** = Running close to limits, leave headroom

### CPU Usage
- **< 60%** = Plenty of capacity
- **60-80%** = Good utilization
- **> 80%** = May see frame drops

### Per-Match Overhead
Typical:
- **RAM:** 300-400 MB per match
- **CPU:** 10-15% per match (on 8-core system = ~1-1.2 cores)

## Troubleshooting

### Test stops immediately
- Check if other matches are already running
- Check available RAM: `free -h`

### Test won't start
- Make sure backend is running
- Check savestate exists: `ls training/data/savestates/mk4_arcade/p1p2state.st`

### High CPU but low match count
- This is normal - emulation is CPU-intensive
- Consider reducing FPS to 15 if needed

## After Testing

Use the recommendations to set your production limits in your match scheduling system.

Example: If test says "6 matches", set max concurrent matches to 5-6 in production.
