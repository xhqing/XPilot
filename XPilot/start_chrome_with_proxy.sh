#!/bin/bash
# Start Google Chrome with system proxy settings
# Usage: ./start_chrome_with_proxy.sh

PROXY_SOCKS="socks5://127.0.0.1:1080"

echo "Checking proxy is ready..."
# Check if proxy is running by testing port connectivity
if ! nc -z 127.0.0.1 1080 2>/dev/null; then
  echo "ERROR: Proxy is not running or not responding."
  echo "Start it first with: proxy start"
  exit 1
fi
echo "Proxy is ready: $PROXY_SOCKS"

echo "Closing existing Chrome windows..."
killall "Google Chrome" 2>/dev/null
sleep 2

echo "Starting Chrome with proxy..."
open -a "Google Chrome" --args \
  --proxy-server="$PROXY_SOCKS" \
  --no-first-run \
  --no-default-browser-check

echo "Chrome started. Try visiting youtube.com"
