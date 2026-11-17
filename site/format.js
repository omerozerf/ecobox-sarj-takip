// Turkish number and time formatting. Every clock time is Europe/Istanbul, whatever the
// visitor's own time zone is; the wording mirrors `ecobox/fmt.py` so the page and the bot agree.

export const TZ = 'Europe/Istanbul';

const integer = new Intl.NumberFormat('tr-TR');
const clockFormat = new Intl.DateTimeFormat('tr-TR', {
  timeZone: TZ, hour: '2-digit', minute: '2-digit', hourCycle: 'h23',
});
const dayFormat = new Intl.DateTimeFormat('tr-TR', { timeZone: TZ, day: 'numeric', month: 'short' });
const slotFormat = new Intl.DateTimeFormat('en-US', {
  timeZone: TZ, weekday: 'short', hour: 'numeric', hourCycle: 'h23',
});
const WEEKDAY_INDEX = { Mon: 0, Tue: 1, Wed: 2, Thu: 3, Fri: 4, Sat: 5, Sun: 6 };

export const int = (n) => integer.format(n);

/** Turkish decimal comma, at most `digits` places, trailing zeros dropped: 2.34 -> "2,3". */
export function decimal(value, digits = 1) {
  return new Intl.NumberFormat('tr-TR', { maximumFractionDigits: digits }).format(value);
}

/** 0.854 -> "%85" (half up, like the bot). */
export function pct(share) {
  return `%${Math.floor(share * 100 + 0.5)}`;
}

/** 204 -> "3 sa 24 dk", 45 -> "45 dk", 120 -> "2 sa". */
export function minutes(value) {
  const total = Math.max(0, Math.round(value));
  const h = Math.floor(total / 60);
  const m = total % 60;
  if (h && m) return `${h} sa ${m} dk`;
  return h ? `${h} sa` : `${m} dk`;
}

/** How long ago, for the reading's age: "az önce", "3 dk önce", "1 sa 5 dk önce". */
export function ago(ms) {
  return ms < 60_000 ? 'az önce' : `${minutes(ms / 60_000)} önce`;
}

export const clock = (ms) => clockFormat.format(ms);
export const stamp = (ms) => `${dayFormat.format(ms)} ${clock(ms)}`;
export const hourLabel = (hour) => `${String(hour).padStart(2, '0')}:00`;

/** Local weekday (0 = Monday) and hour of a moment, in Europe/Istanbul. */
export function localSlot(ms) {
  const parts = Object.fromEntries(slotFormat.formatToParts(ms).map((p) => [p.type, p.value]));
  return { weekday: WEEKDAY_INDEX[parts.weekday], hour: Number(parts.hour) % 24 };
}

/** "f blok" -> "F Blok", with the Turkish dotted capital I. */
export function titled(words) {
  return words.split(' ').map((w) => w.charAt(0).toLocaleUpperCase('tr') + w.slice(1)).join(' ');
}

/** {energy: 9.29, currency: "TRY"} -> "9,29 TL/kWh"; null without a tariff. */
export function price(p) {
  if (!p) return null;
  const currency = p.currency === 'TRY' ? 'TL' : p.currency;
  return `${decimal(p.energy, 2)} ${currency}/kWh`;
}

// Third-person possessive after a number read aloud, as `ecobox/fmt.py` picks it:
// 40 -> "'ı", 45 -> "'i", 100 -> "'ü".
const POSSESSIVE_BY_UNIT = { 1: 'i', 2: 'si', 3: 'ü', 4: 'ü', 5: 'i', 6: 'sı', 7: 'si', 8: 'i', 9: 'u' };
const POSSESSIVE_BY_TENS = { 1: 'u', 2: 'si', 3: 'u', 4: 'ı', 5: 'si', 6: 'ı', 7: 'i', 8: 'i', 9: 'ı' };

export function possessive(n) {
  if (n === 0) return "'ı";
  if (n % 1000 === 0) return "'i";
  if (n % 100 === 0) return "'ü";
  if (n % 10) return `'${POSSESSIVE_BY_UNIT[n % 10]}`;
  return `'${POSSESSIVE_BY_TENS[Math.floor((n % 100) / 10)]}`;
}
