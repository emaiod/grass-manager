#!/usr/bin/with-contenv bashio
set -e
cd /app
exec uvicorn main:app --host 0.0.0.0 --port 8100 --proxy-headers --forwarded-allow-ips='*'
