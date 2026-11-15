// Backend for the standalone phone app: the same API as server.py, but running
// inside the app and talking to the TV directly through the native TVHttp plugin.
// In the browser version (served by server.py) this file is loaded but unused.
(() => {
  const STORE = 'philipsRemoteConfig';
  // Shared key used by every Philips Android TV to sign the pairing PIN.
  const PAIRING_SECRET =
    'ZmVay1EQVFOaZhwQ4Kv81ypLAZNczV9sG4KkseXWn1NEk6cXmPKO/MCa9sryslvLCFMnNe4Z4CPXzToowvhHvA==';
  const KEYS = new Set([
    'Standby', 'Mute', 'VolumeUp', 'VolumeDown', 'ChannelStepUp', 'ChannelStepDown',
    'CursorUp', 'CursorDown', 'CursorLeft', 'CursorRight', 'Confirm', 'Back', 'Home',
    'Source', 'Info', 'Options', 'Find', 'Adjust', 'WatchTV', 'Teletext', 'Subtitle',
    'Play', 'Pause', 'PlayPause', 'Stop', 'FastForward', 'Rewind', 'Record',
    'RedColour', 'GreenColour', 'YellowColour', 'BlueColour', 'Dot',
    ...Array.from({length: 10}, (_, d) => 'Digit' + d),
  ]);

  class TVError extends Error {
    constructor(message, status = 502) { super(message); this.status = status; }
  }

  const sleep = ms => new Promise(r => setTimeout(r, ms));
  const native = () => window.Capacitor.Plugins.TVHttp;

  let config = {host: '', scheme: 'http', port: 1925, version: 1, deviceId: '', authKey: ''};
  try { Object.assign(config, JSON.parse(localStorage.getItem(STORE) || '{}')); } catch (e) { /* first run */ }
  let pendingPair = null;

  function save() {
    try { localStorage.setItem(STORE, JSON.stringify(config)); } catch (e) { /* storage unavailable */ }
  }

  function publicConfig() {
    return {host: config.host, scheme: config.scheme, port: config.port, version: config.version,
            paired: !!config.authKey, demo: !config.host, native: true};
  }

  // ---- Talking to a real TV ------------------------------------------------

  async function tv(path, body, opts = {}) {
    const scheme = opts.scheme || config.scheme, port = opts.port || config.port;
    if (!path.startsWith('/')) path = `/${config.version}/${path}`;
    const req = {url: `${scheme}://${config.host}:${port}${path}`,
                 method: body === undefined ? 'GET' : 'POST', timeout: opts.timeout || 4};
    if (body !== undefined) req.body = JSON.stringify(body);
    const auth = opts.auth !== undefined ? opts.auth
      : (config.authKey ? [config.deviceId, config.authKey] : null);
    if (auth) { req.username = auth[0]; req.password = auth[1]; }

    let res;
    try { res = await native().request(req); }
    catch (e) { throw new TVError(`Cannot reach TV at ${config.host}: ${e.message || e}`, 504); }
    if (res.status === 401) throw new TVError("The TV requires pairing. Use 'Pair' in the settings.", 401);
    if (res.status < 200 || res.status >= 300) throw new TVError(`TV answered HTTP ${res.status} for ${path}`);
    try { return res.data && res.data.trim() ? JSON.parse(res.data) : {}; } catch (e) { return {}; }
  }

  // Find which JointSpace flavour the TV speaks.
  async function detect(host) {
    const previous = {...config};
    config.host = host;
    let last = null;
    for (const [scheme, port, version] of [['http', 1925, 6], ['http', 1925, 5], ['http', 1925, 1],
                                            ['https', 1926, 6]]) {
      let info = {};
      try { info = await tv(`/${version}/system`, undefined, {scheme, port, timeout: 2}); }
      catch (e) { last = e; if (e.status !== 401) continue; }
      const needsPairing = JSON.stringify(info.featuring || {}).includes('pairing');
      if (scheme === 'http' && needsPairing) continue;  // Android TV: control lives on https/1926
      Object.assign(config, {scheme, port, version});
      return info;
    }
    config = previous;
    throw last || new TVError(`No Philips TV found at ${host}`);
  }

  // Scan the phone's Wi-Fi network (x.y.z.1-254) for anything answering on the TV's ports.
  async function discover() {
    let own = '';
    try { own = (await native().localAddress()).address || ''; } catch (e) { /* handled below */ }
    if (!/^\d+\.\d+\.\d+\.\d+$/.test(own)) {
      throw new TVError('Cannot search: the phone is not connected to Wi-Fi.', 400);
    }
    const prefix = own.slice(0, own.lastIndexOf('.'));
    const targets = [];
    for (const [scheme, port] of [['http', 1925], ['https', 1926]]) {
      for (let n = 1; n <= 254; n++) {
        const ip = `${prefix}.${n}`;
        if (ip !== own) targets.push({ip, url: `${scheme}://${ip}:${port}/6/system`});
      }
    }
    const candidates = [];
    for (let i = 0; i < targets.length; i += 64) {
      await Promise.all(targets.slice(i, i + 64).map(async t => {
        try {
          const res = await native().request({url: t.url, method: 'GET', timeout: 1.5});
          if (res.status > 0 && !candidates.includes(t.ip)) candidates.push(t.ip);
        } catch (e) { /* nothing there */ }
      }));
    }
    const found = [];
    const saved = {...config};
    for (const ip of candidates) {
      try {
        const info = await detect(ip);
        found.push({host: ip, name: String((info && info.name) || '')});
      } catch (e) { /* not a Philips TV */ }
      config = {...saved};
    }
    return found;
  }

  async function pairRequest() {
    const chars = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789';
    const id = Array.from(crypto.getRandomValues(new Uint8Array(16)), b => chars[b % chars.length]).join('');
    const device = {device_name: 'virtual-remote', device_os: 'Android', app_name: 'VirtualRemote',
                    type: 'native', app_id: 'app.id', id};
    const resp = await tv('pair/request', {scope: ['read', 'write', 'control'], device}, {auth: null});
    if (!resp.auth_key) throw new TVError('TV refused the pairing request: ' + (resp.error_text || 'unknown error'));
    pendingPair = {device, authKey: resp.auth_key, timestamp: resp.timestamp};
  }

  async function pairGrant(pin) {
    if (!pendingPair) throw new TVError('Start pairing first.', 400);
    const secret = Uint8Array.from(atob(PAIRING_SECRET), c => c.charCodeAt(0));
    const key = await crypto.subtle.importKey('raw', secret, {name: 'HMAC', hash: 'SHA-1'}, false, ['sign']);
    const mac = await crypto.subtle.sign('HMAC', key, new TextEncoder().encode(String(pendingPair.timestamp) + pin));
    const hex = Array.from(new Uint8Array(mac), b => b.toString(16).padStart(2, '0')).join('');
    const body = {auth: {auth_AppId: '1', pin, auth_timestamp: pendingPair.timestamp, auth_signature: btoa(hex)},
                  device: pendingPair.device};
    const auth = [pendingPair.device.id, pendingPair.authKey];
    const resp = await tv('pair/grant', body, {auth});
    if ((resp.error_id || 'SUCCESS') !== 'SUCCESS') {
      throw new TVError('Pairing failed: ' + (resp.error_text || resp.error_id), 400);
    }
    Object.assign(config, {deviceId: auth[0], authKey: auth[1]});
    pendingPair = null;
    save();
  }

  async function tvStatus() {
    const status = {power: true, channel: null, name: '', volume: null, muted: false};
    try {
      const vol = await tv('audio/volume', undefined, {timeout: 2});
      Object.assign(status, {volume: vol.current ?? null, muted: !!vol.muted});
    } catch (e) { if (e.status === 401 || e.status === 504) throw e; }
    try {
      let ch;
      if (config.version >= 5) {
        ch = (await tv('activities/tv', undefined, {timeout: 2})).channel || {};
      } else {
        const cur = (await tv('channels/current', undefined, {timeout: 2})).id;
        ch = (await tv('channels', undefined, {timeout: 2}))[cur] || {};
      }
      Object.assign(status, {channel: ch.preset ?? null, name: ch.name || ''});
    } catch (e) { /* channel info is optional */ }
    return status;
  }

  // ---- Simulated TV (demo mode) --------------------------------------------

  const CHANNELS = ['News One', 'Cinema', 'Sports Live', 'Nature', 'Kids', 'Music Box',
                    'Documentary', 'Comedy', 'World', 'Science', 'Travel', 'Classics'];
  const demo = {
    power: true, channel: 1, prev: 1, volume: 18, muted: false, entry: '', entryAt: 0,
    tune(n) { if (n >= 1 && n <= 999) { this.prev = this.channel; this.channel = n; } },
    commit() { if (this.entry) { this.tune(parseInt(this.entry, 10)); this.entry = ''; } },
    key(key) {
      if (key === 'Standby') { this.power = !this.power; this.entry = ''; return; }
      if (!this.power) return;
      if (key.startsWith('Digit')) { this.entry = (this.entry + key.slice(5)).slice(-3); this.entryAt = Date.now(); return; }
      if (key === 'Confirm') this.commit();
      else if (key === 'ChannelStepUp') { this.entry = ''; this.tune(this.channel < 999 ? this.channel + 1 : 1); }
      else if (key === 'ChannelStepDown') { this.entry = ''; this.tune(this.channel > 1 ? this.channel - 1 : 999); }
      else if (key === 'Back') { if (this.entry) this.entry = ''; else this.tune(this.prev); }
      else if (key === 'VolumeUp') { this.volume = Math.min(60, this.volume + 1); this.muted = false; }
      else if (key === 'VolumeDown') { this.volume = Math.max(0, this.volume - 1); this.muted = false; }
      else if (key === 'Mute') this.muted = !this.muted;
    },
    status() {
      if (this.entry && Date.now() - this.entryAt > 2000) this.commit();
      return {power: this.power, channel: this.channel, name: CHANNELS[(this.channel - 1) % CHANNELS.length],
              volume: this.volume, muted: this.muted, entry: this.entry};
    },
  };

  // ---- API (mirrors server.py) ---------------------------------------------

  async function sendKey(key) {
    if (!KEYS.has(key)) throw new TVError('Unknown key: ' + key, 400);
    if (config.host) await tv('input/key', {key}); else demo.key(key);
  }

  async function setChannel(number) {
    const n = parseInt(number, 10);
    if (!(n >= 1 && n <= 9999)) throw new TVError('Channel must be between 1 and 9999', 400);
    for (const d of String(n)) {
      await sendKey('Digit' + d);
      if (config.host) await sleep(250);
    }
    await sendKey('Confirm');
  }

  async function api(path, body) {
    const post = body !== undefined;
    if (path === '/api/config' && !post) return publicConfig();
    if (path === '/api/config') {
      const host = String(body.host || '').trim();
      if (host) {
        if (!/^[A-Za-z0-9.:-]+$/.test(host)) throw new TVError('Invalid TV address', 400);
        await detect(host);
      } else {
        config.host = '';
      }
      Object.assign(config, {deviceId: '', authKey: ''});
      pendingPair = null;
      save();
      return publicConfig();
    }
    if (path === '/api/discover' && post) return {tvs: await discover()};
    if (path === '/api/status' && !post) return config.host ? tvStatus() : demo.status();
    if (path === '/api/key' && post) { await sendKey(String(body.key || '')); return {ok: true}; }
    if (path === '/api/channel' && post) { await setChannel(body.number); return {ok: true}; }
    if (path === '/api/pair/request' && post) { await pairRequest(); return {ok: true}; }
    if (path === '/api/pair/grant' && post) { await pairGrant(String(body.pin || '').trim()); return publicConfig(); }
    throw new TVError('Not found', 404);
  }

  // Run calls one at a time, like the server does, so key presses keep their order.
  let queue = Promise.resolve();
  window.directBackend = {
    call(path, body) {
      const run = queue.then(() => api(path, body));
      queue = run.catch(() => {});
      return run;
    },
  };
})();
