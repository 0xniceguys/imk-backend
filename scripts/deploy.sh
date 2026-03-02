#!/bin/bash
# deploy.sh — one-command deploy: push local changes to EC2
# Usage: ./scripts/deploy.sh
set -e

echo "=== Pushing to GitHub ==="
git add -A
git commit -m "deploy: $(date '+%Y-%m-%d %H:%M')" 2>/dev/null || echo "(nothing to commit)"
git push origin main

echo "=== Pulling on EC2 + restarting service ==="
ssh tekkenlord 'cd /home/ubuntu/imk && git pull origin main && sudo systemctl restart imk && sudo systemctl status imk --no-pager | head -4'

echo "=== Deploy complete ==="
curl -s https://immortalkombat.mercle.ai/health
