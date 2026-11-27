/*
 * RaceBoard: a virtual LED matrix timing board.
 *
 * A framebuffer of individually addressed LEDs, text drawn from the Adafruit
 * GFX 5x7 bitmap font, and every lit LED painted as a physical dot. No styled
 * HTML text anywhere.
 *
 * 256x144 is 16:9, so it fills a TV, and it is the smallest size that fits the
 * whole 22-car field at once: two columns of eleven, no paging during a race.
 */

const W = 256, H = 144, TILE = 16;
const CHAR_W = 6, CHAR_H = 8;

const CFG = {
  POLL_MS: 1000,
  FIELD_MS: 8000,     // how long each timing column (interval, gap, last lap) holds
  PAGE_MS: 12000,     // between sessions: how long each page holds
  RC_MS: 4000,        // a long race control message pages in chunks this fast
  STALE_S: 90,        // feed silent this long during a session: say so
};

const C = {
  text: 0xeaf4ff, dim: 0x6d8595, faint: 0x2a3640,
  amber: 0xffb000, purple: 0xb04dff, cyan: 0x66d9ff,
  green: 0x2bd94a, yellow: 0xffd400, red: 0xff2a1f, white: 0xf2f2f2, black: 0x000000,
};
const TYRE = { S: C.red, M: C.yellow, H: C.white, I: C.green, W: 0x2a7fff };

const cvs = document.getElementById('panel');
const ctx = cvs.getContext('2d');
const fb = new Uint32Array(W * H);    // 0 = LED off
let shown = new Uint32Array(W * H);   // what's on the canvas now, to skip idle repaints
let pitch = 4;
let gridCanvas = null;

/* ---------- framebuffer ---------- */

function clear() { fb.fill(0); }

function setPx(x, y, color) {
  x |= 0; y |= 0;
  if (x < 0 || y < 0 || x >= W || y >= H) return;
  fb[y * W + x] = color | 0x1000000;   // high bit marks "lit", so black is drawable
}

function fillRect(x, y, w, h, color) {
  for (let j = 0; j < h; j++) for (let i = 0; i < w; i++) setPx(x + i, y + j, color);
}

function hLine(y, color) { for (let x = 0; x < W; x++) setPx(x, y, color); }

function drawText(x, y, text, color, scale = 1) {
  text = String(text);
  for (let i = 0; i < text.length; i++) {
    let code = text.charCodeAt(i);
    if (code < 32 || code > 126) code = 63;
    const gx = x + i * CHAR_W * scale;
    for (let col = 0; col < 5; col++) {
      const bits = GLCD_FONT[code * 5 + col];
      if (!bits) continue;
      for (let row = 0; row < 8; row++) {
        if (!(bits & (1 << row))) continue;
        if (scale === 1) setPx(gx + col, y + row, color);
        else fillRect(gx + col * scale, y + row * scale, scale, scale, color);
      }
    }
  }
  return text.length * CHAR_W * scale;
}

const textW = (t, scale = 1) => String(t).length * CHAR_W * scale;
const drawRight = (xr, y, t, c, s = 1) => drawText(xr - textW(t, s) + s, y, t, c, s);
const drawCentered = (y, t, c, s = 1) => drawText(Math.round((W - textW(t, s)) / 2), y, t, c, s);
// Classic Chromecasts run a pre-2020 Chromium: no nullish or optional
// chaining operators in this file, or the whole script fails to parse.
const orIf = (v, d) => (v == null ? d : v);
const fit = (t, n) => String(orIf(t, '')).slice(0, n);
const hex = h => parseInt(h || 'ffffff', 16);

/* ---------- canvas ---------- */

function layoutCanvas() {
  pitch = Math.max(2, Math.floor(Math.min(innerWidth * 0.96 / W, innerHeight * 0.94 / H)));
  cvs.width = W * pitch;
  cvs.height = H * pitch;
  buildGrid();
  shown = new Uint32Array(W * H).fill(0xffffffff);   // force a full repaint
  render();
}

// The unlit LEDs never change, so bake them once and blit.
function buildGrid() {
  gridCanvas = document.createElement('canvas');
  gridCanvas.width = cvs.width;
  gridCanvas.height = cvs.height;
  const g = gridCanvas.getContext('2d');
  g.fillStyle = '#020202';
  g.fillRect(0, 0, cvs.width, cvs.height);
  // kept very dark: on a TV with a poor black level anything brighter turns
  // the whole panel grey
  const r = Math.max(0.6, pitch * 0.26);
  g.fillStyle = '#0a0a0a';
  for (let y = 0; y < H; y++) {
    for (let x = 0; x < W; x++) {
      g.beginPath();
      g.arc(x * pitch + pitch / 2, y * pitch + pitch / 2, r, 0, Math.PI * 2);
      g.fill();
    }
  }
  g.strokeStyle = 'rgba(0,0,0,0.5)';
  g.lineWidth = 1;
  for (let x = TILE; x < W; x += TILE) { g.beginPath(); g.moveTo(x * pitch, 0); g.lineTo(x * pitch, cvs.height); g.stroke(); }
  for (let y = TILE; y < H; y += TILE) { g.beginPath(); g.moveTo(0, y * pitch); g.lineTo(cvs.width, y * pitch); g.stroke(); }
}

function render() {
  // a Chromecast has little GPU to spare; when nothing changed, draw nothing
  let same = true;
  for (let i = 0; i < fb.length; i++) if (fb[i] !== shown[i]) { same = false; break; }
  if (same) return;
  shown.set(fb);

  ctx.drawImage(gridCanvas, 0, 0);
  const r = Math.max(0.8, pitch * 0.36);
  ctx.shadowBlur = pitch * 0.8;
  // batch by colour: one path per colour is far cheaper than one per LED
  const byColour = new Map();
  for (let i = 0; i < fb.length; i++) {
    const v = fb[i];
    if (!v) continue;
    const c = v & 0xffffff;
    let list = byColour.get(c);
    if (!list) byColour.set(c, list = []);
    list.push(i);
  }
  for (const [c, list] of byColour) {
    const css = '#' + c.toString(16).padStart(6, '0');
    ctx.fillStyle = css;
    ctx.shadowColor = c ? css : 'transparent';
    ctx.beginPath();
    for (const i of list) {
      const x = i % W, y = (i / W) | 0;
      const cx = x * pitch + pitch / 2, cy = y * pitch + pitch / 2;
      ctx.moveTo(cx + r, cy);
      ctx.arc(cx, cy, r, 0, Math.PI * 2);
    }
    ctx.fill();
  }
  ctx.shadowBlur = 0;
}

/* ---------- formatting ---------- */

function fmtClock(s) {
  if (s == null) return '';
  const h = Math.floor(s / 3600), m = Math.floor(s / 60) % 60, sec = s % 60;
  const mm = String(m).padStart(2, '0'), ss = String(sec).padStart(2, '0');
  return h ? `${h}:${mm}:${ss}` : `${mm}:${ss}`;
}

function fmtCountdown(ms) {
  const s = Math.max(0, Math.floor(ms / 1000));
  const d = Math.floor(s / 86400), h = Math.floor(s / 3600) % 24;
  const m = Math.floor(s / 60) % 60, sec = s % 60;
  const p = n => String(n).padStart(2, '0');
  return d ? `${d}D ${p(h)}:${p(m)}:${p(sec)}` : `${p(h)}:${p(m)}:${p(sec)}`;
}

/* ---------- timing tower ---------- */

const ROW_Y0 = 23, ROW_PITCH = 10, ROWS = 11, COL_X = [0, 129];

function timingFields(t) {
  return t.type === 'Race'
    ? [['interval', 'INTERVAL'], ['gap', 'GAP'], ['last', 'LAST LAP']]
    : [['best', 'BEST LAP'], ['gap', 'GAP']];
}

const isDone = t => ['Finished', 'Finalised', 'Ends'].includes(t.status);
// a car stopped on track mid-session is news; after the flag it's just a DNF
const isOut = (t, r) => r.retired || (r.stopped && isDone(t));

function rowValue(t, r, field, index) {
  if (isOut(t, r)) return ['OUT', C.dim];
  if (r.stopped) return ['STOPPED', C.red];
  if (r.in_pit) return ['IN PIT', C.cyan];
  if (r.pit_out) return ['PIT EXIT', C.cyan];
  let v = r[field] || '';
  if (!v && index === 0) {
    // the leader has no gap or interval to show
    if (t.type === 'Race' && field !== 'last') v = 'LEADER';
    else if (field === 'gap') v = r.best || '';
  }
  return [v, r.out ? C.dim : C.text];
}

function drawTowerRow(t, r, i, field, index) {
  const x0 = COL_X[i < ROWS ? 0 : 1];
  const y = ROW_Y0 + (i % ROWS) * ROW_PITCH;
  const dim = r.out || isOut(t, r);
  fillRect(x0, y, 2, 7, dim ? C.faint : hex(r.colour));
  drawRight(x0 + 15, y, orIf(r.pos, '-'), dim ? C.dim : C.text);
  drawText(x0 + 22, y, r.tla, dim ? C.dim : (r.fastest ? C.purple : C.text));
  const [v, col] = rowValue(t, r, field, index);
  drawRight(x0 + 93, y, fit(v, 8), col);
  if (r.tyre) {
    drawText(x0 + 100, y, r.tyre, dim ? C.dim : (TYRE[r.tyre] || C.text));
    if (r.tyre_laps != null) drawRight(x0 + 117, y, fit(r.tyre_laps, 2), C.dim);
  }
}

function buildTimingFrame(data, fieldIdx, rcIdx) {
  const t = data.timing;
  clear();

  // header: where and what, then laps or the session clock
  drawText(0, 1, fit(`${t.meeting} ${t.session}`, 30), C.text);
  const stale = !data.feed.replay && data.feed.age_s != null && data.feed.age_s > CFG.STALE_S;
  if (stale) drawRight(W - 1, 1, 'NO FEED', C.red);
  else if (t.type === 'Race' && t.laps) drawRight(W - 1, 1, `LAP ${orIf(t.lap, 0)}/${t.laps}`, C.amber);
  else if (remainingNow() != null) drawRight(W - 1, 1, fmtClock(remainingNow()), C.amber);

  // track status: green is just a word, anything else takes the whole band
  const fields = timingFields(t);
  const [field, label] = fields[fieldIdx % fields.length];
  const tc = t.track.colour;
  if (tc === 'green' || !t.track.text) {
    drawText(0, 11, t.track.text || '', C.green);
    drawRight(W - 1, 11, label, C.dim);
  } else {
    const bg = { yellow: C.yellow, red: C.red, white: C.white }[tc] || C.text;
    fillRect(0, 10, W, 10, bg);
    drawCentered(11, t.track.text, C.black);
  }
  hLine(21, C.faint);

  t.rows.slice(0, ROWS * 2).forEach((r, i) => drawTowerRow(t, r, i, field, i));

  // footer: race control, or the weather when race control has nothing to say
  hLine(133, C.faint);
  const rc = t.race_control.length ? t.race_control[t.race_control.length - 1] : null;
  if (rc && rc.text) {
    const chunks = chunk(rc.text.toUpperCase(), 42);
    drawText(0, 135, chunks[rcIdx % chunks.length], rcColour(rc));
  } else if (t.weather.air) {
    const w = t.weather;
    drawText(0, 135, `AIR ${Math.round(w.air)}C  TRACK ${Math.round(w.track)}C${w.rain ? '  RAIN' : ''}`, C.dim);
  }
}

function rcColour(rc) {
  const f = (rc.flag || '').toUpperCase();
  if (f.includes('RED')) return C.red;
  if (f.includes('YELLOW') || (rc.cat || '') === 'SafetyCar') return C.yellow;
  if (f === 'GREEN' || f === 'CLEAR') return C.green;
  if (f === 'BLUE') return 0x2a7fff;
  if (f.includes('CHEQUERED')) return C.white;
  return C.amber;
}

// split on word boundaries into lines of at most n characters
function chunk(text, n) {
  const out = [];
  let line = '';
  for (const word of text.split(/\s+/)) {
    if (!line) line = word.slice(0, n);
    else if (line.length + 1 + word.length <= n) line += ' ' + word;
    else { out.push(line); line = word.slice(0, n); }
  }
  if (line) out.push(line);
  return out.length ? out : [''];
}

/* ---------- between sessions ---------- */

function idlePages(idle) {
  const pages = [];
  if (idle.next) pages.push('next');
  if (idle.drivers.length) pages.push('drivers');
  if (idle.teams.length) pages.push('teams');
  if (idle.last) pages.push('last');
  return pages;
}

function sessionLength(name) { return name === 'RACE' ? 2 * 3600e3 : 3600e3; }

function buildNextFrame(n) {
  const now = Date.now();
  drawText(0, 1, `ROUND ${n.round}`, C.dim);
  drawRight(W - 1, 1, n.season, C.dim);
  const name = n.name.toUpperCase().replace(' GRAND PRIX', ' GP');
  drawCentered(13, fit(name, 21), C.text, 2);
  drawCentered(33, fit(`${n.circuit}`.toUpperCase(), 42), C.dim);

  const sessions = n.sessions.map(s => ({ ...s, t: Date.parse(s.utc) }));
  const live = sessions.find(s => s.t <= now && now < s.t + sessionLength(s.name));
  const next = sessions.find(s => s.t > now);
  if (live) {
    drawCentered(50, `${live.name} IS ON NOW`, C.green, 2);
  } else if (next) {
    drawCentered(46, `${next.name} STARTS IN`, C.dim);
    drawCentered(57, fmtCountdown(next.t - now), C.amber, 2);
  }

  hLine(77, C.faint);
  let y = 82;
  for (const s of sessions) {
    const past = s.t + sessionLength(s.name) <= now;
    const col = s === live ? C.green : s === next ? C.amber : past ? C.faint : C.text;
    drawText(40, y, s.name, col);
    drawRight(W - 41, y, s.local, col);
    y += 10;
  }
}

function buildDriversFrame(idle) {
  drawText(0, 1, 'DRIVER STANDINGS', C.text);
  if (idle.standings_round) drawRight(W - 1, 1, `AFTER R${idle.standings_round}`, C.dim);
  hLine(21, C.faint);
  idle.drivers.slice(0, ROWS * 2).forEach((d, i) => {
    const x0 = COL_X[i < ROWS ? 0 : 1], y = ROW_Y0 + (i % ROWS) * ROW_PITCH;
    fillRect(x0, y, 2, 7, hex(d.colour));
    drawRight(x0 + 15, y, orIf(d.pos, '-'), C.text);
    drawText(x0 + 22, y, d.code, C.text);
    drawRight(x0 + 93, y, d.points, C.amber);
  });
}

function buildTeamsFrame(idle) {
  drawText(0, 1, 'CONSTRUCTORS', C.text);
  if (idle.standings_round) drawRight(W - 1, 1, `AFTER R${idle.standings_round}`, C.dim);
  hLine(21, C.faint);
  idle.teams.slice(0, ROWS).forEach((t, i) => {
    const x0 = 40, y = ROW_Y0 + i * ROW_PITCH;
    fillRect(x0, y, 2, 7, hex(t.colour));
    drawRight(x0 + 15, y, orIf(t.pos, '-'), C.text);
    drawText(x0 + 22, y, fit(t.name, 14), C.text);
    drawRight(x0 + 175, y, t.points, C.amber);
  });
}

function buildLastFrame(idle) {
  const l = idle.last;
  drawText(0, 1, fit(l.name.toUpperCase().replace(' GRAND PRIX', ' GP'), 30), C.text);
  drawRight(W - 1, 1, 'RESULT', C.dim);
  hLine(21, C.faint);
  l.results.slice(0, ROWS * 2).forEach((r, i) => {
    const x0 = COL_X[i < ROWS ? 0 : 1], y = ROW_Y0 + (i % ROWS) * ROW_PITCH;
    fillRect(x0, y, 2, 7, hex(r.colour));
    drawRight(x0 + 15, y, r.pos, C.text);
    drawText(x0 + 22, y, r.code, i === 0 ? C.amber : C.text);
    drawRight(x0 + 117, y, fit(r.result, 12), i === 0 ? C.amber : C.dim);
  });
}

function buildIdleFrame(idle, pageIdx) {
  clear();
  const pages = idlePages(idle);
  if (!pages.length) return buildMessageFrame(['RACEBOARD', 'waiting for data']);
  const page = pages[pageIdx % pages.length];
  if (page === 'next') buildNextFrame(idle.next);
  else if (page === 'drivers') buildDriversFrame(idle);
  else if (page === 'teams') buildTeamsFrame(idle);
  else buildLastFrame(idle);
}

function buildMessageFrame(lines) {
  clear();
  const top = Math.round((H - lines.length * 20) / 2);
  lines.forEach((l, i) => drawCentered(top + i * 20, l, i ? C.dim : C.text, i ? 1 : 2));
}

/* ---------- data + cycling ---------- */

let data = null;
let linkDown = false;
let build = null;
let receivedAt = 0;
let fieldIdx = 0, pageIdx = 0, rcIdx = 0;
let lastField = Date.now(), lastPage = Date.now(), lastRc = Date.now();
let lastMode = null;

// The session clock only arrives with each poll; count it down in between.
function remainingNow() {
  const t = data && data.timing;
  if (!t || t.remaining_s == null) return null;
  if (t.status !== 'Started') return t.remaining_s;
  return Math.max(0, t.remaining_s - Math.floor((Date.now() - receivedAt) / 1000));
}

async function poll() {
  try {
    const res = await fetch('/api/board', { cache: 'no-store' });
    const next = await res.json();
    // a cast page never reloads on its own; pick up a new build when it lands
    if (next.build) {
      if (build && next.build !== build) { location.reload(); return; }
      build = next.build;
    }
    data = next;
    receivedAt = Date.now();
    linkDown = false;
    if (data.mode !== lastMode) { lastMode = data.mode; pageIdx = 0; fieldIdx = 0; }
  } catch (e) {
    linkDown = true;
  }
  paint();
}

function tick() {
  const now = Date.now();
  if (now - lastField >= CFG.FIELD_MS) { fieldIdx++; lastField = now; }
  if (now - lastPage >= CFG.PAGE_MS) { pageIdx++; lastPage = now; }
  if (now - lastRc >= CFG.RC_MS) { rcIdx++; lastRc = now; }
  paint();
}

function paint() {
  if (linkDown && !data) buildMessageFrame(['NO LINK', 'backend down']);
  else if (!data) buildMessageFrame(['...']);
  else if (linkDown) buildMessageFrame(['NO LINK', 'backend down']);
  else if (data.mode === 'timing' && data.timing) buildTimingFrame(data, fieldIdx, rcIdx);
  else buildIdleFrame(data.idle, pageIdx);
  render();
}

addEventListener('resize', layoutCanvas);
layoutCanvas();
paint();
poll();
setInterval(poll, CFG.POLL_MS);
setInterval(tick, 500);
