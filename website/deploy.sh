#!/bin/bash
# IMK Website deploy script — run from /home/ubuntu/imk/website
set -e
cd /home/ubuntu/imk/website

echo "→ Building Next.js..."
npm run build

echo "→ Syncing static assets..."
cp -r .next/static .next/standalone/.next/static
mkdir -p .next/standalone/public
cp -r public/characters .next/standalone/public/characters
cp -r public/figma .next/standalone/public/figma

echo "→ Restarting service..."
sudo systemctl restart imk-website

echo "✓ Website deployed — https://immortalkombat.timesnap.xyz"
