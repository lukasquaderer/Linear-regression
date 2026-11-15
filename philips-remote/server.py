#!/usr/bin/env python3
"""Virtual remote control for Philips TVs (JointSpace API).

Serves a web remote on http://localhost:8080 and relays button presses to the
TV over the local network. Runs in demo mode (a simulated TV) until a TV
address is configured. Standard library only.

    python3 server.py                 # demo mode, configure the TV in the UI
    python3 server.py --tv 192.168.1.50
    python3 server.py --listen 0.0.0.0   # reachable from a phone on the LAN
"""

import argparse
import base64
import hashlib
import hmac
import json
import os
import random
import ssl
import string
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
WWW = os.path.join(HERE, "www")
STATIC = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/index.html": ("index.html", "text/html; charset=utf-8"),
    "/backend.js": ("backend.js", "text/javascript; charset=utf-8"),
    "/manifest.webmanifest": ("manifest.webmanifest", "application/manifest+json"),
    "/icon-180.png": ("icon-180.png", "image/png"),
    "/icon-512.png": ("icon-512.png", "image/png"),
}
CONFIG_PATH = os.path.join(HERE, "config.json")
TIMEOUT = 4

# Shared key used by every Philips Android TV to sign the pairing PIN.
PAIRING_SECRET = base64.b64decode(
    "ZmVay1EQVFOaZhwQ4Kv81ypLAZNczV9sG4KkseXWn1NEk6cXmPKO/MCa9sryslvLCFMnNe4Z4CPXzToowvhHvA=="
)

KEYS = {
    "Standby", "Mute", "VolumeUp", "VolumeDown", "ChannelStepUp", "ChannelStepDown",
    "CursorUp", "CursorDown", "CursorLeft", "CursorRight", "Confirm", "Back", "Home",
    "Source", "Info", "Options", "Find", "Adjust", "WatchTV", "Teletext", "Subtitle",
    "Play", "Pause", "PlayPause", "Stop", "FastForward", "Rewind", "Record",
    "RedColour", "GreenColour", "YellowColour", "BlueColour", "Dot",
    *("Digit%d" % d for d in range(10)),
}


class TVError(Exception):
    def __init__(self, message, status=502):
        super().__init__(message)
        self.status = status


# --------------------------------------------------------------------------
# Config
# --------------------------------------------------------------------------

lock = threading.Lock()
config = {"host": "", "scheme": "http", "port": 1925, "version": 1,
          "device_id": "", "auth_key": ""}
pending_pair = {}
phone_url = ""  # set when the server is reachable from other devices on the network


def load_config():
    try:
        with open(CONFIG_PATH) as f:
            config.update(json.load(f))
    except (OSError, ValueError):
        pass


def save_config():
    with open(CONFIG_PATH, "w") as f:
        json.dump(config, f, indent=2)
    os.chmod(CONFIG_PATH, 0o600)


def public_config():
    return {"host": config["host"], "scheme": config["scheme"], "port": config["port"],
            "version": config["version"], "paired": bool(config["auth_key"]),
            "demo": not config["host"], "phone_url": phone_url}


# --------------------------------------------------------------------------
# Talking to a real TV
# --------------------------------------------------------------------------

def tv_request(path, body=None, *, scheme=None, port=None, auth=None, timeout=TIMEOUT):
    """Call the TV. `path` excludes the API version prefix when it starts without '/'."""
    scheme = scheme or config["scheme"]
    port = port or config["port"]
    if not path.startswith("/"):
        path = "/%s/%s" % (config["version"], path)
    url = "%s://%s:%s%s" % (scheme, config["host"], port, path)

    handlers = [urllib.request.ProxyHandler({})]  # the TV is on the LAN: never proxy
    if scheme == "https":
        # Philips TVs present a self-signed certificate, so it cannot be verified.
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        ctx.set_ciphers("DEFAULT:@SECLEVEL=0")
        handlers.append(urllib.request.HTTPSHandler(context=ctx))
    if auth is None and config["auth_key"]:
        auth = (config["device_id"], config["auth_key"])
    if auth:
        mgr = urllib.request.HTTPPasswordMgrWithDefaultRealm()
        mgr.add_password(None, url, auth[0], auth[1])
        handlers.append(urllib.request.HTTPDigestAuthHandler(mgr))

    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method="POST" if data else "GET",
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.build_opener(*handlers).open(req, timeout=timeout) as resp:
            raw = resp.read()
    except urllib.error.HTTPError as e:
        if e.code == 401:
            raise TVError("The TV requires pairing. Use 'Pair' in the settings.", 401)
        raise TVError("TV answered HTTP %s for %s" % (e.code, path))
    except (urllib.error.URLError, OSError) as e:
        raise TVError("Cannot reach TV at %s: %s" % (config["host"], getattr(e, "reason", e)), 504)
    try:
        return json.loads(raw) if raw.strip() else {}
    except ValueError:
        return {}


def detect(host):
    """Find which JointSpace flavour the TV speaks."""
    previous = dict(config)
    config["host"] = host
    last = None
    for scheme, port, version in (("http", 1925, 6), ("http", 1925, 5), ("http", 1925, 1),
                                  ("https", 1926, 6)):
        try:
            info = tv_request("/%s/system" % version, scheme=scheme, port=port, timeout=2)
        except TVError as e:
            last = e
            if e.status != 401:
                continue
            info = {}
        needs_pairing = "pairing" in json.dumps(info.get("featuring", {}))
        if scheme == "http" and needs_pairing:
            continue  # Android TV: control endpoints live on https/1926
        config.update(scheme=scheme, port=port, version=version)
        return info
    config.clear()
    config.update(previous)
    raise last or TVError("No Philips TV found at %s" % host)


def discover():
    """Scan this computer's local /24 network for Philips TVs (JointSpace ports)."""
    import socket
    from concurrent.futures import ThreadPoolExecutor

    def probe(target):
        ip, port = target
        try:
            with socket.create_connection((ip, port), timeout=0.6):
                return ip
        except OSError:
            return None

    candidates = []
    for own in lan_addresses():
        prefix = own.rsplit(".", 1)[0]
        targets = [("%s.%d" % (prefix, n), port) for n in range(1, 255) for port in (1925, 1926)
                   if "%s.%d" % (prefix, n) != own]
        with ThreadPoolExecutor(max_workers=128) as pool:
            for ip in pool.map(probe, targets):
                if ip and ip not in candidates:
                    candidates.append(ip)

    found = []
    saved = dict(config)
    for ip in candidates:
        try:
            info = detect(ip)
            found.append({"host": ip, "name": str(info.get("name", ""))})
        except TVError:
            pass
        config.clear()
        config.update(saved)
    return found


def pair_request():
    device_id = "".join(random.SystemRandom().choice(string.ascii_letters + string.digits)
                        for _ in range(16))
    device = {"device_name": "virtual-remote", "device_os": "Android",
              "app_name": "VirtualRemote", "type": "native",
              "app_id": "app.id", "id": device_id}
    resp = tv_request("pair/request", {"scope": ["read", "write", "control"], "device": device},
                      auth=False)
    if "auth_key" not in resp:
        raise TVError("TV refused the pairing request: %s" % resp.get("error_text", resp))
    pending_pair.clear()
    pending_pair.update(device=device, auth_key=resp["auth_key"], timestamp=resp["timestamp"])


def pair_grant(pin):
    if not pending_pair:
        raise TVError("Start pairing first.", 400)
    to_sign = (str(pending_pair["timestamp"]) + str(pin)).encode()
    digest = hmac.new(PAIRING_SECRET, to_sign, hashlib.sha1).hexdigest()
    body = {"auth": {"auth_AppId": "1", "pin": str(pin),
                     "auth_timestamp": pending_pair["timestamp"],
                     "auth_signature": base64.b64encode(digest.encode()).decode()},
            "device": pending_pair["device"]}
    auth = (pending_pair["device"]["id"], pending_pair["auth_key"])
    resp = tv_request("pair/grant", body, auth=auth)
    if resp.get("error_id", "SUCCESS") != "SUCCESS":
        raise TVError("Pairing failed: %s" % resp.get("error_text", resp["error_id"]), 400)
    config.update(device_id=auth[0], auth_key=auth[1])
    pending_pair.clear()
    save_config()


def tv_status():
    status = {"power": True, "channel": None, "name": "", "volume": None, "muted": False}
    try:
        vol = tv_request("audio/volume", timeout=2)
        status.update(volume=vol.get("current"), muted=bool(vol.get("muted")))
    except TVError as e:
        if e.status in (401, 504):
            raise
    try:
        if config["version"] >= 5:
            ch = tv_request("activities/tv", timeout=2).get("channel", {})
            status.update(channel=ch.get("preset"), name=ch.get("name", ""))
        else:
            cur = tv_request("channels/current", timeout=2).get("id")
            ch = tv_request("channels", timeout=2).get(cur, {})
            status.update(channel=ch.get("preset"), name=ch.get("name", ""))
    except TVError:
        pass
    return status


# --------------------------------------------------------------------------
# Simulated TV (demo mode)
# --------------------------------------------------------------------------

class DemoTV:
    CHANNELS = ["News One", "Cinema", "Sports Live", "Nature", "Kids", "Music Box",
                "Documentary", "Comedy", "World", "Science", "Travel", "Classics"]

    def __init__(self):
        self.power, self.channel, self.prev = True, 1, 1
        self.volume, self.muted = 18, False
        self.entry, self.entry_at = "", 0.0

    def _tune(self, number):
        if 1 <= number <= 999:
            self.prev, self.channel = self.channel, number

    def _commit(self):
        if self.entry:
            self._tune(int(self.entry))
            self.entry = ""

    def key(self, key):
        if key == "Standby":
            self.power = not self.power
            self.entry = ""
            return
        if not self.power:
            return
        if key.startswith("Digit"):
            self.entry = (self.entry + key[5:])[-3:]
            self.entry_at = time.time()
            return
        if key == "Confirm":
            self._commit()
        elif key == "ChannelStepUp":
            self.entry = ""
            self._tune(self.channel + 1 if self.channel < 999 else 1)
        elif key == "ChannelStepDown":
            self.entry = ""
            self._tune(self.channel - 1 if self.channel > 1 else 999)
        elif key == "Back":
            if self.entry:
                self.entry = ""
            else:
                self._tune(self.prev)
        elif key == "VolumeUp":
            self.volume, self.muted = min(60, self.volume + 1), False
        elif key == "VolumeDown":
            self.volume, self.muted = max(0, self.volume - 1), False
        elif key == "Mute":
            self.muted = not self.muted

    def status(self):
        if self.entry and time.time() - self.entry_at > 2:
            self._commit()
        return {"power": self.power, "channel": self.channel,
                "name": self.CHANNELS[(self.channel - 1) % len(self.CHANNELS)],
                "volume": self.volume, "muted": self.muted, "entry": self.entry}


demo = DemoTV()


# --------------------------------------------------------------------------
# API
# --------------------------------------------------------------------------

def send_key(key):
    if key not in KEYS:
        raise TVError("Unknown key: %s" % key, 400)
    if config["host"]:
        tv_request("input/key", {"key": key})
    else:
        demo.key(key)


def set_channel(number):
    digits = str(int(number))
    if not 1 <= int(digits) <= 9999:
        raise TVError("Channel must be between 1 and 9999", 400)
    for d in digits:
        send_key("Digit" + d)
        if config["host"]:
            time.sleep(0.25)
    send_key("Confirm")


def api(method, path, body):
    if path == "/api/config" and method == "GET":
        return public_config()
    if path == "/api/config" and method == "POST":
        host = str(body.get("host", "")).strip()
        if host:
            if not all(c.isalnum() or c in ".-:" for c in host):
                raise TVError("Invalid TV address", 400)
            detect(host)
        else:
            config["host"] = ""
        config.update(device_id="", auth_key="")
        pending_pair.clear()
        save_config()
        return public_config()
    if path == "/api/discover" and method == "POST":
        return {"tvs": discover()}
    if path == "/api/status" and method == "GET":
        return tv_status() if config["host"] else demo.status()
    if path == "/api/key" and method == "POST":
        send_key(str(body.get("key", "")))
        return {"ok": True}
    if path == "/api/channel" and method == "POST":
        try:
            set_channel(body.get("number"))
        except (TypeError, ValueError):
            raise TVError("Channel must be a number", 400)
        return {"ok": True}
    if path == "/api/pair/request" and method == "POST":
        pair_request()
        return {"ok": True}
    if path == "/api/pair/grant" and method == "POST":
        pair_grant(str(body.get("pin", "")).strip())
        return public_config()
    raise TVError("Not found", 404)


class Handler(BaseHTTPRequestHandler):
    def _send(self, status, payload, ctype="application/json"):
        data = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _handle(self, method):
        path = self.path.split("?")[0]
        if method == "GET" and path in STATIC:
            name, ctype = STATIC[path]
            with open(os.path.join(WWW, name), "rb") as f:
                return self._send(200, f.read(), ctype)
        body = {}
        if method == "POST":
            # Only accept JSON so other websites cannot drive the TV via simple form posts.
            if "application/json" not in self.headers.get("Content-Type", ""):
                return self._send(415, {"error": "JSON required"})
            try:
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
                if not isinstance(body, dict):
                    raise ValueError
            except ValueError:
                return self._send(400, {"error": "Invalid JSON"})
        try:
            with lock:
                result = api(method, path, body)
            self._send(200, result)
        except TVError as e:
            self._send(e.status, {"error": str(e)})

    def do_GET(self):
        self._handle("GET")

    def do_POST(self):
        self._handle("POST")

    def log_message(self, fmt, *args):
        if "/api/status" not in (args[0] if args else ""):
            super().log_message(fmt, *args)


def lan_addresses():
    """Best-effort guess of this computer's address on the local network."""
    import socket
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("192.0.2.1", 9))  # no packet is sent for a UDP connect
            return [s.getsockname()[0]]
    except OSError:
        return []


def main():
    parser = argparse.ArgumentParser(description="Virtual remote for Philips TVs")
    parser.add_argument("--tv", help="IP address of the TV (omit for demo mode / saved config)")
    parser.add_argument("--listen", default="127.0.0.1", help="address to serve on")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--open", action="store_true", help="open the remote in the browser")
    args = parser.parse_args()

    load_config()
    if args.tv and args.tv != config["host"]:
        try:
            detect(args.tv)
            config.update(device_id="", auth_key="")
            save_config()
        except TVError as e:
            print("Warning: %s (starting in demo mode)" % e)

    server = ThreadingHTTPServer((args.listen, args.port), Handler)
    mode = "TV at %s" % config["host"] if config["host"] else "demo mode"
    print("Philips remote running on http://%s:%s (%s)" % (args.listen, args.port, mode))
    if args.listen == "0.0.0.0":
        global phone_url
        for ip in lan_addresses():
            phone_url = "http://%s:%s" % (ip, args.port)
            print("  On your phone, open %s" % phone_url)
    if args.open:
        import webbrowser
        threading.Timer(0.5, webbrowser.open, ["http://localhost:%s" % args.port]).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
