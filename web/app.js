/*
 * Team room display.
 *
 * Reads data/events.json, data/settings.json and data/photos.json and keeps the screen up
 * to date. The Pi writes those files: the events are a copy of the team's Google
 * Calendar, the rest comes from the admin pages. The rules implemented here, such as
 * when an event ends, what "Next up" shows, and when data counts as stale, are written
 * out in docs/SPEC.md. Keep the two in sync.
 *
 * The page only talks to its own web server, and if a fetch fails it keeps showing the
 * last good data. Everything from the JSON is inserted with textContent, never as HTML.
 */
'use strict';

const POLL_MS = 30 * 1000;         // how often to re-read the JSON files (the Pi rewrites them every 30 s)
const RETRY_MS = 10 * 1000;        // ...until the first successful load
const FETCH_TIMEOUT_MS = 15 * 1000; // give up on a request that hangs, so polling carries on
const STALE_AFTER_MINUTES = 60;    // a warning appears when generated_at is older than this
const MAX_FONT_SCALE = 1.5;        // larger and the layout no longer fits (SPEC §11)
const LIST_MAX_ROWS = 7;
const LIST_WINDOW_DAYS = 30;
const DAILY_RELOAD_HOUR = 4;       // reload the page every day at 04:00

const DEFAULT_SETTINGS = { competitions: [], photo_interval_s: 15, font_scale: 1, banner_text: '' };
const MAX_COUNTDOWNS = 2; // the admin page has two (one per car); the header fits two lines
const WEEKDAYS = ['Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday'];
const WEEKDAYS_SHORT = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];
const MONTHS = ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September',
  'October', 'November', 'December'];
const MONTHS_SHORT = MONTHS.map((month) => month.slice(0, 3));

const $ = (id) => document.getElementById(id);

const state = {
  loaded: false,        // have we ever loaded events.json?
  fetchFailed: false,   // did the most recent poll fail?
  events: [],           // parsed, sorted by start
  generatedAt: null,    // Date the Pi last wrote events.json
  syncedAt: null,       // Date the Pi last read the Google Calendar (null: never)
  timeSynced: true,
  settings: { ...DEFAULT_SETTINGS },
};


/* ---- Time --------------------------------------------------------------------- */

// All times are local wall-clock strings, e.g. 2026-10-14T18:30 (SPEC §2). They are read
// as local time on the Pi; nothing is converted to or from UTC.
function parseLocal(text) {
  const m = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})(?::(\d{2}))?$/.exec(text);
  if (!m) return null;
  return new Date(+m[1], m[2] - 1, +m[3], +m[4], +m[5], +(m[6] || 0));
}

function parseDate(text) {
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(text);
  if (!m) return null;
  const date = new Date(+m[1], m[2] - 1, +m[3]);
  // A date that doesn't exist (Feb 30) would silently roll over to March 2.
  return date.getMonth() === m[2] - 1 && date.getDate() === +m[3] ? date : null;
}

function startOfDay(date) {
  return new Date(date.getFullYear(), date.getMonth(), date.getDate());
}

function addDays(date, days) {
  return new Date(date.getFullYear(), date.getMonth(), date.getDate() + days, date.getHours(), date.getMinutes());
}

// Whole calendar days from a's date to b's date. Counted on dates, not hours, so a
// daylight-saving change can't make it off by one.
function daysBetween(a, b) {
  const utc = (d) => Date.UTC(d.getFullYear(), d.getMonth(), d.getDate());
  return Math.round((utc(b) - utc(a)) / 86400000);
}

// ?now=2026-11-01T01:58 starts the page clock at that time, then it ticks normally.
// For testing only (SPEC §8); the kiosk never uses it.
const testStart = parseLocal(new URLSearchParams(location.search).get('now') || '');
const clockOffset = testStart ? testStart.getTime() - Date.now() : 0;

function now() {
  return new Date(Date.now() + clockOffset);
}


/* ---- Events: when they end, what's next (SPEC §2–4) ---------------------------- */

// When an event is over: its end; its start if it has no end; for all-day events,
// midnight after the last day.
function endsAt(ev) {
  if (ev.allDay) return addDays(startOfDay(ev.end || ev.start), 1);
  return ev.end || ev.start;
}

function isHappening(ev, t) {
  return ev.start <= t && t < ev.endsAt;
}

function lastDay(ev) {
  return addDays(ev.endsAt, -1);
}

// Next up = the earliest-starting event that hasn't ended, skipping all-day events that
// are already under way (a week-long event shouldn't hide the next meeting); those are
// listed under Today instead, unless nothing else is left. The list = every other event
// that starts within the next 30 days (or is already going).
function pickEvents(t) {
  const upcoming = state.events.filter((ev) => ev.endsAt > t);
  const hero = upcoming.find((ev) => !(ev.allDay && isHappening(ev, t))) || upcoming[0] || null;
  const windowEnd = addDays(t, LIST_WINDOW_DAYS);
  return { hero, rest: upcoming.filter((ev) => ev !== hero && ev.start < windowEnd) };
}


/* ---- Wording ------------------------------------------------------------------ */

function timeText(d) { // '6:30 PM', or '6 PM' on the hour
  const hour = d.getHours() % 12 || 12;
  const minutes = d.getMinutes() ? ':' + String(d.getMinutes()).padStart(2, '0') : '';
  return `${hour}${minutes} ${d.getHours() < 12 ? 'AM' : 'PM'}`;
}

function timeRange(a, b) { // '6:30 – 8 PM', '11 AM – 1 PM'
  const sameHalf = (a.getHours() < 12) === (b.getHours() < 12);
  return `${sameHalf ? timeText(a).slice(0, -3) : timeText(a)} – ${timeText(b)}`;
}

function shortDate(d) { // 'Thu, Sep 24'
  return `${WEEKDAYS_SHORT[d.getDay()]}, ${MONTHS_SHORT[d.getMonth()]} ${d.getDate()}`;
}

function dateRange(a, b) { // 'Oct 2 – 4', 'Oct 30 – Nov 2'
  const start = `${MONTHS_SHORT[a.getMonth()]} ${a.getDate()}`;
  return a.getMonth() === b.getMonth() ? `${start} – ${b.getDate()}` : `${start} – ${MONTHS_SHORT[b.getMonth()]} ${b.getDate()}`;
}

function dayLabel(day, t) {
  const days = daysBetween(t, day);
  if (days === 0) return 'Today';
  if (days === 1) return 'Tomorrow';
  return shortDate(day);
}

// The big "in 2 h" / "Tomorrow" text on the Next up card (SPEC §3).
function relativeLabel(ev, t) {
  if (isHappening(ev, t)) {
    if (!ev.allDay) return 'Now';
    const daysLeft = daysBetween(t, lastDay(ev));
    if (daysLeft <= 0) return 'Today';
    return daysLeft < 7 ? `Through ${WEEKDAYS[lastDay(ev).getDay()]}` : `Through ${shortDate(lastDay(ev))}`;
  }
  const days = daysBetween(t, ev.start);
  if (!ev.allDay) {
    const minutes = Math.round((ev.start - t) / 60000);
    if (minutes < 1) return 'Starting now';
    if (minutes < 60) return `in ${minutes} min`;
    if (days === 0) {
      const hours = Math.floor(minutes / 60);
      const rest = minutes % 60;
      return hours < 3 && rest ? `in ${hours} h ${rest} min` : `in ${hours} h`;
    }
  }
  if (days === 1) return 'Tomorrow';
  if (days < 7) return WEEKDAYS[ev.start.getDay()];
  return `in ${days} days`;
}

// 'Thu, Sep 24 · 6:30 – 8 PM', 'Fri, Oct 2 – Sun, Oct 4', ... (SPEC §3)
function absoluteWhen(ev) {
  if (ev.allDay) {
    const last = lastDay(ev);
    return daysBetween(ev.start, last) === 0 ? `${shortDate(ev.start)} · All day` : `${shortDate(ev.start)} – ${shortDate(last)}`;
  }
  if (!ev.end) return `${shortDate(ev.start)} · ${timeText(ev.start)}`;
  if (daysBetween(ev.start, ev.end) === 0) return `${shortDate(ev.start)} · ${timeRange(ev.start, ev.end)}`;
  return `${shortDate(ev.start)}, ${timeText(ev.start)} – ${shortDate(ev.end)}, ${timeText(ev.end)}`;
}

// The time column of the upcoming list (SPEC §4).
function listTime(ev, t) {
  if (ev.allDay) {
    const last = lastDay(ev);
    return daysBetween(ev.start, last) === 0 ? 'All day' : dateRange(ev.start, last);
  }
  if (isHappening(ev, t)) {
    return daysBetween(t, ev.end) === 0 ? `Until ${timeText(ev.end)}` : `Until ${WEEKDAYS_SHORT[ev.end.getDay()]}`;
  }
  if (!ev.end) return timeText(ev.start);
  if (daysBetween(ev.start, ev.end) === 0) return timeRange(ev.start, ev.end);
  return `${timeText(ev.start)} – ${WEEKDAYS_SHORT[ev.end.getDay()]}`;
}


/* ---- Reading the JSON files ---------------------------------------------------- */
// Anything that isn't the expected shape throws, so the page keeps its current data.

const isObject = (value) => typeof value === 'object' && value !== null && !Array.isArray(value);
const text = (value) => (typeof value === 'string' ? value : '');

function readEvent(raw) {
  const start = isObject(raw) ? parseLocal(raw.start) : null;
  const end = start && raw.end !== null ? parseLocal(raw.end) : null;
  if (!start || (raw.end !== null && !end) || typeof raw.title !== 'string' || !raw.title
      || typeof raw.all_day !== 'boolean') {
    throw new Error(`events.json has an event in the wrong format: ${JSON.stringify(raw).slice(0, 120)}`);
  }
  // An end at or before the start is no end at all (the same rule the Pi applies).
  const ev = { title: raw.title, start, end: end && end > start ? end : null, allDay: raw.all_day, location: text(raw.location) };
  ev.endsAt = endsAt(ev);
  return ev;
}

function readEvents(data) {
  const generatedAt = isObject(data) ? parseLocal(data.generated_at) : null;
  const syncedAt = generatedAt && data.synced_at !== null ? parseLocal(data.synced_at) : null;
  if (!generatedAt || (data.synced_at !== null && !syncedAt) || typeof data.time_synced !== 'boolean'
      || !Array.isArray(data.events)) {
    throw new Error('events.json is not in the expected format');
  }
  const events = data.events.map(readEvent).sort((a, b) => a.start - b.start);
  return () => {
    Object.assign(state, { events, generatedAt, syncedAt, timeSynced: data.time_synced, loaded: true });
  };
}

function readSettings(data) {
  if (!isObject(data)) throw new Error('settings.json is not in the expected format');
  const scale = Number(data.font_scale);
  const interval = Number(data.photo_interval_s);
  const settings = {
    competitions: (Array.isArray(data.competitions) ? data.competitions : []).filter(isObject)
      .slice(0, MAX_COUNTDOWNS).map((c) => ({ name: text(c.name), date: text(c.date) })),
    banner_text: text(data.banner_text),
    font_scale: scale >= 0.5 ? Math.min(scale, MAX_FONT_SCALE) : DEFAULT_SETTINGS.font_scale,
    photo_interval_s: interval >= 5 && interval <= 600 ? interval : DEFAULT_SETTINGS.photo_interval_s,
  };
  return () => {
    state.settings = settings;
    if (photos.timer) scheduleNextPhoto(); // a changed photo interval applies right away
  };
}

function readPhotos(data) {
  if (!isObject(data) || !Array.isArray(data.photos)) throw new Error('photos.json is not in the expected format');
  const photos = data.photos
    .filter((p) => isObject(p) && /^[A-Za-z0-9_-][A-Za-z0-9._-]*$/.test(p.file))
    .map((p) => ({ file: p.file, caption: text(p.caption) }));
  return () => setPhotos(photos);
}

const DATA_FILES = [
  { name: 'events.json', read: readEvents },
  { name: 'settings.json', read: readSettings },
  { name: 'photos.json', read: readPhotos },
];

const lastText = {}; // what each file said last time, so an unchanged file costs no work

async function fetchText(name) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), FETCH_TIMEOUT_MS);
  try {
    const response = await fetch(`data/${name}?t=${Date.now()}`, { cache: 'no-store', signal: controller.signal });
    if (!response.ok) throw new Error(`data/${name}: HTTP ${response.status}`);
    return await response.text();
  } finally {
    clearTimeout(timer);
  }
}

// Re-read the three files. Only a file that changed is parsed and applied, and the screen
// is only redrawn when something changed (or a fetch started or stopped failing), so the
// Pi does no layout work for the usual poll where nothing is new.
async function poll() {
  const results = await Promise.allSettled(DATA_FILES.map((file) => fetchText(file.name)));
  let failed = false;
  let changed = false;
  results.forEach((result, i) => {
    const { name, read } = DATA_FILES[i];
    try {
      if (result.status === 'rejected') throw result.reason;
      if (result.value === lastText[name]) return; // same as last time
      read(JSON.parse(result.value))();
      lastText[name] = result.value;
      changed = true;
    } catch (error) {
      failed = true;
      console.error(`Could not use data/${name}; keeping what is on screen.`, error);
    }
  });
  const failureChanged = failed !== state.fetchFailed;
  state.fetchFailed = failed;
  try {
    if (changed || failureChanged) render();
  } finally {
    setTimeout(poll, state.loaded ? POLL_MS : RETRY_MS);
  }
}


/* ---- Drawing the screen -------------------------------------------------------- */

function el(tag, className, content) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (content !== undefined) node.textContent = content;
  return node;
}

function setText(id, value) {
  const node = $(id);
  node.textContent = value;
  node.hidden = !value;
}

function renderClock(t) {
  const hour = t.getHours() % 12 || 12;
  $('clock-hm').textContent = `${hour}:${String(t.getMinutes()).padStart(2, '0')}`;
  $('clock-ampm').textContent = t.getHours() < 12 ? 'AM' : 'PM';
  $('clock-date').textContent = `${WEEKDAYS[t.getDay()]}, ${MONTHS[t.getMonth()]} ${t.getDate()}`;
}

// "FSAE Michigan in 231 days": one line per competition still to come, sooner first (SPEC §5)
function renderCountdown(t) {
  const node = $('countdown');
  const coming = state.timeSynced ? state.settings.competitions
    .map((c) => ({ name: c.name || 'Competition', date: parseDate(c.date) }))
    .filter((c) => c.date && daysBetween(t, c.date) >= 0)
    .map((c) => ({ name: c.name, days: daysBetween(t, c.date) }))
    .sort((a, b) => a.days - b.days) : [];
  node.replaceChildren(...coming.map(({ name, days }) => {
    // The name is cut short with "…" if it doesn't fit; the number of days never is.
    const when = el('span', 'countdown-when');
    if (days === 0) when.append(' is ', el('strong', '', 'today!'));
    else if (days === 1) when.append(' is ', el('strong', '', 'tomorrow'));
    else when.append(' in ', el('strong', '', String(days)), ' days');
    const line = el('li');
    line.append(el('span', 'countdown-name', name), when);
    return line;
  }));
  node.classList.toggle('is-pair', coming.length > 1);
  node.hidden = coming.length === 0;
}

function renderHero(ev, t) {
  const hero = $('hero');
  hero.classList.toggle('is-loading', !state.loaded);
  hero.classList.toggle('is-empty', state.loaded && !ev);
  hero.classList.toggle('is-now', Boolean(ev) && isHappening(ev, t));
  if (!ev) {
    $('hero-kicker').textContent = state.loaded ? 'All clear' : 'Next up';
    $('hero-title').textContent = state.loaded ? 'Nothing on the schedule' : 'Loading the schedule…';
    setText('hero-relative', '');
    setText('hero-absolute', state.loaded ? 'New events show up here within a minute of being added.' : '');
    setText('hero-where', '');
    return;
  }
  $('hero-kicker').textContent = isHappening(ev, t) ? 'Happening now' : 'Next up';
  $('hero-title').textContent = ev.title;
  setText('hero-relative', relativeLabel(ev, t));
  setText('hero-absolute', absoluteWhen(ev));
  setText('hero-where', ev.location);
}

function agendaRow(ev, t, dayText) {
  const row = el('li', dayText ? 'agenda-row first-of-day' : 'agenda-row');
  const title = el('span', 'agenda-title', ev.title);
  if (ev.location) title.append(el('span', 'agenda-location', ` · ${ev.location}`));
  row.append(el('span', 'agenda-day', dayText), el('span', 'agenda-time', listTime(ev, t)), title);
  return row;
}

function listOverflows() {
  const box = $('upcoming').getBoundingClientRect();
  const more = $('agenda-more');
  const last = more.hidden ? $('agenda').lastElementChild : more;
  return Boolean(last) && last.getBoundingClientRect().bottom > box.bottom + 0.5;
}

// Up to 7 rows grouped by day, then "+N more". Rows that don't fit on screen at the
// current text size are dropped from the bottom and counted in "+N more" (SPEC §4).
function renderList(hero, rest, t) {
  $('upcoming').hidden = !hero;
  const today = startOfDay(t);
  let previousDay = null;
  const rows = rest.slice(0, LIST_MAX_ROWS).map((ev) => {
    const day = ev.start < today ? today : startOfDay(ev.start);
    const label = previousDay && day.getTime() === previousDay.getTime() ? '' : dayLabel(day, t);
    previousDay = day;
    return agendaRow(ev, t, label);
  });
  $('agenda').replaceChildren(...rows);

  const showMore = (hiddenCount) => {
    if (rest.length === 0) setText('agenda-more', `Nothing else in the next ${LIST_WINDOW_DAYS} days.`);
    else setText('agenda-more', hiddenCount > 0 ? `+${hiddenCount} more in the next ${LIST_WINDOW_DAYS} days` : '');
  };
  showMore(rest.length - rows.length);
  let shown = rows.length;
  while (shown > 0 && listOverflows()) {
    $('agenda').lastElementChild.remove();
    shown -= 1;
    showMore(rest.length - shown);
  }
  // Not even one row fits (a big text size with a banner): better no list than a cut-off one.
  if (shown === 0 && rest.length > 0) $('upcoming').hidden = true;
}

function renderStatus(t) {
  // Old data: the Pi has stopped writing the files, or it still writes them but hasn't
  // been able to read the Google Calendar, so the events in them are old (SPEC §7).
  const minutesSince = (moment) => (t - moment) / 60000;
  const old = state.loaded && (minutesSince(state.generatedAt) > STALE_AFTER_MINUTES
    || !state.syncedAt || minutesSince(state.syncedAt) > STALE_AFTER_MINUTES);
  const dot = $('status-dot');
  if (!state.loaded) dot.dataset.state = state.fetchFailed ? 'offline' : 'waiting';
  else dot.dataset.state = state.fetchFailed ? 'offline' : old ? 'stale' : 'ok';

  const badge = $('stale-badge');
  badge.classList.toggle('is-offline', state.fetchFailed);
  if (state.fetchFailed) setText('stale-badge', 'Can’t load new data. Showing the last copy.');
  else setText('stale-badge', old ? 'Schedule may be out of date' : '');
  // While all is well the status line says nothing, and the bottom row (banner plus
  // status line) takes no space unless one of them has something to show.
  $('status').hidden = badge.hidden;
  $('bottom').hidden = $('banner').hidden && badge.hidden;
}

function render(t = now()) {
  const settings = state.settings;
  document.documentElement.style.setProperty('--font-scale', String(settings.font_scale));
  setText('banner', settings.banner_text);
  renderClock(t);
  renderCountdown(t);
  const { hero, rest } = pickEvents(t);
  renderHero(hero, t);
  renderStatus(t);
  renderList(hero, rest, t); // last: it measures the finished layout
}


/* ---- Photos (SPEC §6) ------------------------------------------------------------ */
// Two stacked <img> elements take turns: the hidden one loads and decodes the next
// photo, then the two crossfade. The same two elements are reused forever.

const photos = {
  order: [],            // the photos in the order they are shown (shuffled)
  files: '',            // the file names last seen, to tell a caption edit from a real change
  captions: new Map(),  // file name → caption (captions can change without a reshuffle)
  broken: new Set(),    // files that failed to load; not asked for again until the list changes
  index: -1, front: null, current: null,
  timer: 0, busy: false,
  shownAt: 0,           // when the rotation last moved on
};

function shuffle(list) {
  for (let i = list.length - 1; i > 0; i -= 1) {
    const j = Math.floor(Math.random() * (i + 1));
    [list[i], list[j]] = [list[j], list[i]];
  }
  return list;
}

function setPhotos(list) {
  photos.captions = new Map(list.map((p) => [p.file, p.caption]));
  if (photos.current) setText('photo-caption', photos.captions.get(photos.current.file) || '');
  const files = list.map((p) => p.file).join('\n');
  const changed = files !== photos.files;
  if (changed) { // photos were added to or taken out of the folder (not just captioned): shuffle afresh
    photos.files = files;
    photos.order = shuffle(list.slice());
    photos.index = -1;
    photos.broken.clear(); // a photo may have been replaced
  }
  if (photos.busy) return; // the photo being loaded right now picks up the new list
  if (!photos.order.length) {
    if (changed) {
      clearTimeout(photos.timer);
      photos.timer = 0;
      showPlaceholder();
    }
  } else if (photos.timer) {
    scheduleNextPhoto(); // the current photo stays up, but a changed interval applies right away
  } else {
    nextPhoto();
  }
}

// The next change is photo_interval_s after the rotation last moved on, so a new interval
// from settings.json counts from then, not from when it was read.
function scheduleNextPhoto() {
  clearTimeout(photos.timer);
  const due = photos.shownAt + state.settings.photo_interval_s * 1000;
  photos.timer = setTimeout(nextPhoto, Math.max(0, due - Date.now()));
}

function showPlaceholder() {
  $('photo-a').classList.remove('is-visible');
  $('photo-b').classList.remove('is-visible');
  photos.front = null;
  photos.current = null;
  $('photo-placeholder').hidden = false;
  $('photo-caption').hidden = true;
}

async function showPhoto(photo) {
  if (photos.current && photos.current.file === photo.file) { // e.g. only one photo: don't fade it into itself
    setText('photo-caption', photos.captions.get(photo.file) || '');
    photos.current = photo;
    return true;
  }
  const back = photos.front === $('photo-a') ? $('photo-b') : $('photo-a');
  back.src = `photos/${encodeURIComponent(photo.file)}`;
  try {
    await back.decode();
  } catch (error) {
    console.error(`Skipping photo ${photo.file}: it did not load.`, error);
    photos.broken.add(photo.file);
    return false;
  }
  back.classList.add('is-visible');
  if (photos.front) photos.front.classList.remove('is-visible');
  photos.front = back;
  photos.current = photo;
  $('photo-placeholder').hidden = true;
  setText('photo-caption', photos.captions.get(photo.file) || '');
  return true;
}

async function nextPhoto() {
  clearTimeout(photos.timer);
  photos.timer = 0;
  photos.busy = true;
  try {
    let shown = false;
    for (let tries = 0; tries < photos.order.length && !shown; tries += 1) {
      photos.index = (photos.index + 1) % photos.order.length;
      const photo = photos.order[photos.index];
      if (!photos.broken.has(photo.file)) shown = await showPhoto(photo);
    }
    if (!shown || !photos.order.length) showPlaceholder(); // nothing loadable, or the list emptied meanwhile
  } finally {
    photos.busy = false;
  }
  photos.shownAt = Date.now();
  if (photos.order.length) scheduleNextPhoto();
}


/* ---- Start ------------------------------------------------------------------------ */

const loadedAt = now();
const nextReload = new Date(loadedAt.getFullYear(), loadedAt.getMonth(), loadedAt.getDate(), DAILY_RELOAD_HOUR);
if (nextReload <= loadedAt) nextReload.setDate(nextReload.getDate() + 1);
let lastMinute = -1;

// Once a second, check whether the minute has changed; only then redraw. Nothing on the
// page shows seconds, so redrawing more often would just make the Pi repaint for nothing.
function tick() {
  const t = now();
  const minute = Math.floor(t.getTime() / 60000);
  if (minute !== lastMinute) {
    lastMinute = minute;
    render(t); // the clock, relative times, what has ended, the countdown
  }
  if (t >= nextReload && !testStart) location.reload();
}

showPlaceholder();
tick();
setInterval(tick, 1000);
poll();
window.addEventListener('resize', () => render());
document.fonts.ready.then(() => render());
