#!/bin/bash
# Double-click on a Mac to start the remote and open it in the browser.
cd "$(dirname "$0")"
exec python3 server.py --listen 0.0.0.0 --open
