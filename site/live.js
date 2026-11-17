// Live status from the collector's day files. No DOM here: reading, mapping through the
// builder's `states` table, and the one figure the page derives itself ("since when").

const DAY_MS = 86_400_000;

export const utcDay = (ms) => new Date(ms).toISOString().slice(0, 10);
export const previousDay = (day) => utcDay(Date.parse(`${day}T00:00:00Z`) - DAY_MS);

/** One day file -> [{t: epoch ms, r: rows}], oldest first. */
export function parseDay(text) {
  return text.split('\n').filter((line) => line.trim()).map((line) => {
    const record = JSON.parse(line);
    return { t: Date.parse(record.t), r: record.r };
  });
}

/**
 * A socket's normalised state in one reading. null: no reading for it (no position, the
 * connector missing from that poll, or the line older than the connector); "unknown": raw
 * values the builder's table does not know.
 */
export function stateOf(record, position, states) {
  if (position == null) return null;
  const row = record.r[position];
  if (row == null) return null;
  const key = row.map((v) => (v == null ? '' : String(v))).join(',');
  return Object.hasOwn(states, key) ? states[key] : 'unknown';
}

/**
 * Since when a socket has been in its newest state, walking the readings backwards.
 * `start` is the first reading of that run. `exact` is false when the walk stopped at the
 * oldest reading given or at a hole longer than `gapMs` (the state before it is not known);
 * `reachedStart` says it was the oldest reading, so an older day file may tell more.
 */
export function runStart(records, position, states, gapMs) {
  let i = records.length - 1;
  const current = stateOf(records[i], position, states);
  while (i > 0) {
    if (records[i].t - records[i - 1].t > gapMs) {
      return { start: records[i].t, exact: false, reachedStart: false };
    }
    if (stateOf(records[i - 1], position, states) !== current) {
      return { start: records[i].t, exact: true, reachedStart: false };
    }
    i -= 1;
  }
  return { start: records[0].t, exact: false, reachedStart: true };
}

export class LiveFeed {
  constructor(baseUrl) {
    this.base = baseUrl.replace(/\/+$/, '');
    this.finished = new Map(); // day files before the newest one no longer change
  }

  /** Records of one UTC day; null when the file does not exist (yet). */
  async day(day, fresh) {
    if (!fresh && this.finished.has(day)) return this.finished.get(day);
    const response = await fetch(`${this.base}/${day}.jsonl`, { cache: fresh ? 'no-cache' : 'default' });
    if (response.status === 404) return null;
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const records = parseDay(await response.text());
    if (!fresh) this.finished.set(day, records);
    return records;
  }

  /**
   * The newest reading for every socket: {at, states: Map(id -> {state, since})}, or null
   * when neither today's nor yesterday's file has a reading. Throws on network failure.
   */
  async read(sockets, states, gapMs, now = Date.now()) {
    let day = utcDay(now);
    let records = await this.day(day, true);
    if (!records?.length) { // just after 00:00 UTC the new file is not written yet
      day = previousDay(day);
      records = await this.day(day, true);
    }
    if (!records?.length) return null;
    const last = records[records.length - 1];
    let longer = null;
    const result = new Map();
    for (const socket of sockets) {
      const state = stateOf(last, socket.position, states);
      let since = null;
      if (state != null && state !== 'unknown') {
        since = runStart(records, socket.position, states, gapMs);
        if (since.reachedStart) {
          longer ??= await this.withOlderDay(day, records);
          since = runStart(longer, socket.position, states, gapMs);
        }
      }
      result.set(socket.id, { state, since });
    }
    return { at: last.t, states: result };
  }

  async withOlderDay(day, records) {
    try {
      return [...((await this.day(previousDay(day), false)) ?? []), ...records];
    } catch {
      return records; // "since" then says "at least"; the live answer itself is unaffected
    }
  }
}
