# Philips TV virtual remote

A web-based remote control for network-connected Philips TVs. It talks to the
TV's built-in JointSpace API over your home network, so no infrared hardware is
needed. Without a TV it runs in **demo mode** against a simulated TV.

## Run

Requires Python 3.8+ and nothing else.

```bash
python3 server.py                    # then open http://localhost:8080
python3 server.py --tv 192.168.1.50  # connect to a TV straight away
python3 server.py --listen 0.0.0.0   # also usable from a phone on the same Wi-Fi
```

Enter the TV's IP address under **TV connection** (find it on the TV under
Settings → Network → View network settings). The app detects the API version
automatically.

## Changing channels

- **CH ⌃ / ⌄** step up and down.
- Number keys type a channel number, **OK** confirms.
- **Go to channel** sends the digits and OK in one go.

Keyboard shortcuts are listed in the app.

## TV requirements

| TV | What to do |
| --- | --- |
| 2009–2015 models, non-Android (API v1/v5, port 1925) | Works directly. On some sets JointSpace must be switched on: enter `5646877223` ("JOINTSPACE") on the original remote while watching TV. |
| 2016+ Android / Google TV (API v6, HTTPS port 1926) | Click **Pair**, type the PIN shown on the TV. Credentials are stored in `config.json`. |
| Saphi / Titan OS models | Usually API v6 on port 1925 without pairing. |

To switch the TV *on* from standby, enable Settings → Network → "Switch on with
Wi-Fi (WoWLAN)"; otherwise most sets stop answering while in standby.

## Notes

- The server only listens on `127.0.0.1` by default. With `--listen 0.0.0.0`
  anyone on your network can use the remote.
- Philips TVs use a self-signed certificate, so the HTTPS connection to the TV
  is not certificate-verified.
