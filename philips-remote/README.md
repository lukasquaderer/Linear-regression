# Philips TV virtual remote

A web-based remote control for network-connected Philips TVs. It talks to the
TV's built-in JointSpace API over your home network, so no infrared hardware is
needed. Without a TV it runs in **demo mode** against a simulated TV.

## Three ways to use it

| | Needs | Talks to the TV from |
| --- | --- | --- |
| **iPhone app** (standalone) | A Mac with Xcode to install it once | The phone itself |
| **Home-screen web app** on a phone | A computer running `server.py` | That computer |
| **Browser** on a computer | Python 3.8+ | That computer |

All three share the same interface (`www/`).

### iPhone app

The app talks to the TV directly over Wi-Fi; no computer is involved once it is
installed. Installing needs a Mac with Xcode 26+ and Node.js 22+. A free Apple ID
is enough (apps installed that way expire after 7 days and need a re-run from
Xcode; a paid developer account removes that limit).

```bash
cd philips-remote
npm install
npx cap sync ios      # copies www/ into the Xcode project
npx cap open ios      # opens Xcode
```

In Xcode: select the **App** target → *Signing & Capabilities* → pick your team
(and change the bundle identifier if Xcode says it is taken), plug in the
iPhone, choose it as the run destination and press ▶. On first launch allow
the "find devices on local networks" prompt, then enter the TV's IP address
under **TV connection**. Run `npx cap sync ios` again after changing anything in
`www/`.

### Home-screen web app (no Mac needed)

On a computer that is on the same network as the TV:

- **Mac:** double-click `start-remote.command` (the first time: right-click →
  Open, because the file was downloaded).
- **Windows:** double-click `start-remote.bat`. It needs Python 3 from
  python.org; allow the firewall prompt so the phone can reach it.
- **Anything else:** `python3 server.py --listen 0.0.0.0 --open`

The remote opens in the browser, searches the network for the TV and connects
to it. Under **TV connection** it shows the address to open in Safari on the
phone; then Share → **Add to Home Screen**. It opens full screen like an app,
but only works while the computer keeps this program running.

### Browser on a computer

```bash
python3 server.py                    # then open http://localhost:8080
python3 server.py --tv 192.168.1.50  # connect to a TV straight away
```

The first time it starts, the remote searches the network for the TV by
itself; **Find my TV on this network** repeats the search. You can also enter
the TV's IP address under **TV connection** (find it on the TV under Settings →
Network → View network settings). The API version is detected automatically.

## Changing channels

- **CH ⌃ / ⌄** step up and down.
- Number keys type a channel number, **OK** confirms.
- **Go to channel** sends the digits and OK in one go.

Keyboard shortcuts are listed in the app.

## TV requirements

| TV | What to do |
| --- | --- |
| 2009–2015 models, non-Android (API v1/v5, port 1925) | Works directly. On some sets JointSpace must be switched on: enter `5646877223` ("JOINTSPACE") on the original remote while watching TV. |
| 2016+ Android / Google TV (API v6, HTTPS port 1926) | Click **Pair**, type the PIN shown on the TV. Credentials are stored in `config.json` (on the phone: in the app's storage). |
| Saphi / Titan OS models | Usually API v6 on port 1925 without pairing. |

To switch the TV *on* from standby, enable Settings → Network → "Switch on with
Wi-Fi (WoWLAN)"; otherwise most sets stop answering while in standby.

## Notes

- The server only listens on `127.0.0.1` by default. With `--listen 0.0.0.0`
  anyone on your network can use the remote.
- In the phone layout the buttons are split over two tabs, **Navigate** and
  **Numbers**; volume and channel up/down are always visible.
- Philips TVs use a self-signed certificate, so the HTTPS connection to the TV
  is not certificate-verified.
