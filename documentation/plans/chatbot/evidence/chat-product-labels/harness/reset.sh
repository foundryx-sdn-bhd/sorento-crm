#!/bin/bash
export AGENT_BROWSER_EXECUTABLE_PATH=/opt/pw-browsers/chromium-1194/chrome-linux/chrome
cd /tmp; AB="npx -y agent-browser@0.27.0 --session cpl"
$AB find role button click --name "Reset" >/dev/null 2>&1; sleep 1
redis-cli --scan --pattern "rate_limit*" | xargs -r redis-cli del >/dev/null
