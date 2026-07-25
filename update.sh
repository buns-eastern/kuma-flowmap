#!/usr/bin/env bash
# Pull the latest code from GitHub and rebuild the running container.
# Run this on the server inside the project folder:  ./update.sh
set -e
cd "$(dirname "$0")"

echo "→ Pulling latest code…"
git pull

echo "→ Rebuilding & restarting the container…"
docker compose up -d --build

echo "✓ Updated. Give it a few seconds, then reload the page."
