#!/bin/sh
set -eu
# A dead container can leave Chromium's profile lock behind.
rm -f /data/chrome/SingletonLock /data/chrome/SingletonSocket /data/chrome/SingletonCookie
exec xvfb-run -a -s '-screen 0 1440x960x24 -nolisten tcp' python /app/app.py
