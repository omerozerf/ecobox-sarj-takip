// "Şu an": the big answer and one tile per socket, built once from data.json so nothing
// moves when the live reading arrives.

import { empty, h, icon, put } from './dom.js';
import { ago, clock, decimal, minutes, price, titled } from './format.js';

export const STALE_MS = 15 * 60_000;
const collator = new Intl.Collator('tr', { numeric: true });
const ICON_BY_STATE = { available: 'free', charging: 'busy', unknown: 'unknown' };

/** Tiles grouped by location word: the broadest word a socket has; DC sockets apart. */
export function socketGroups(sockets) {
  const reach = new Map();
  for (const s of sockets) for (const word of s.groups) reach.set(word, (reach.get(word) ?? 0) + 1);
  const groups = new Map();
  for (const s of sockets) {
    let key = 'other';
    if (s.kind === 'DC') key = 'dc';
    else if (s.groups.length) {
      key = [...s.groups].sort((a, b) => reach.get(b) - reach.get(a) || collator.compare(a, b))[0];
    }
    if (!groups.has(key)) groups.set(key, { key, words: new Set([key]), sockets: [] });
    const group = groups.get(key);
    group.sockets.push(s);
    s.groups.forEach((w) => group.words.add(w));
  }
  const label = (g) => {
    if (g.key === 'dc') return 'DC · hızlı şarj';
    if (g.key === 'other') return 'Diğer';
    return [...g.words].map(titled).join(' · ');
  };
  const rank = (g) => (g.key === 'dc' ? 2 : g.key === 'other' ? 1 : 0);
  return [...groups.values()]
    .map((g) => ({ ...g, label: label(g), sockets: g.sockets.sort((a, b) => collator.compare(a.name, b.name)) }))
    .sort((a, b) => rank(a) - rank(b) || collator.compare(a.label, b.label));
}

/** "22 kW · 9,29 TL/kWh"; each part kept on one line. */
function socketMeta(socket) {
  return [socket.power_kw ? `${decimal(socket.power_kw)} kW` : null, price(socket.price)]
    .filter(Boolean).map((part) => part.replaceAll(' ', '\u00a0')).join(' · ') || 'güç ve fiyat bilinmiyor';
}

export class LiveView {
  constructor(root, data) {
    this.data = data;
    this.labels = { ...data.state_labels, charging: 'dolu', unknown: 'bilinmiyor' };
    this.result = null;
    this.failed = false;
    this.el = {
      eyebrow: root.querySelector('[data-live=eyebrow]'),
      big: root.querySelector('[data-live=big]'),
      sub: root.querySelector('[data-live=sub]'),
      age: root.querySelector('[data-live=age]'),
      banner: root.querySelector('[data-live=banner]'),
      tiles: root.querySelector('[data-live=tiles]'),
    };
    this.tiles = new Map();
    this.buildTiles();
  }

  buildTiles() {
    const groups = socketGroups(this.data.sockets);
    if (!groups.length) {
      put(this.el.tiles, empty('Bu kapsamda priz yok.'));
      return;
    }
    put(this.el.tiles, groups.map((group) => {
      const count = h('span', { class: 'group-count' });
      const metas = new Set(group.sockets.map(socketMeta));
      const shared = metas.size === 1 ? [...metas][0] : null; // said once under the heading
      const list = h('ul', { class: 'tiles' }, group.sockets.map((s) => this.tile(s, shared == null)));
      this.groupCounts ??= [];
      this.groupCounts.push({ count, sockets: group.sockets });
      return h('section', { class: 'group', 'aria-label': group.label },
        h('h3', { class: 'group-title' }, h('span', { text: group.label }), count),
        shared ? h('p', { class: 'group-meta', text: shared }) : null, list);
    }));
  }

  tile(socket, withMeta) {
    const chip = h('span', { class: 'chip' });
    const since = h('span', { class: 'tile-since' });
    const node = h('li', { class: 'tile', 'data-state': 'loading' },
      h('div', { class: 'tile-top' }, h('span', { class: 'tile-name', text: socket.name }), chip),
      h('div', { class: 'tile-meta', text: withMeta ? socketMeta(socket) : '' }),
      since);
    this.tiles.set(socket.id, { node, chip, since });
    this.paintTile(socket.id, undefined);
    return node;
  }

  /**
   * `entry` undefined: still loading; null state: no reading for this socket. `until` is
   * where "since when" is measured to: now, or the reading itself once it is stale (then
   * the time after it is unknown and the figure becomes "at least").
   */
  paintTile(id, entry, until = Date.now(), stale = false) {
    const tile = this.tiles.get(id);
    const state = entry === undefined ? 'loading' : entry.state ?? 'none';
    tile.node.dataset.state = state;
    const label = state === 'loading' ? 'yükleniyor' : state === 'none' ? 'okuma yok' : this.labels[state] ?? state;
    tile.chip.replaceChildren(icon(ICON_BY_STATE[state] ?? (state === 'none' || state === 'loading' ? 'unknown' : 'warn')),
      document.createTextNode(label));
    let since = ' ';
    if (entry?.since) {
      const span = minutes(Math.max(60_000, until - entry.since.start) / 60_000);
      since = entry.since.exact && !stale ? `${span} süredir` : `en az ${span} süredir`;
    }
    tile.since.textContent = since;
  }

  loading() {
    this.setText(this.el.eyebrow, 'Şu an');
    this.setText(this.el.big, 'Okunuyor…');
  }

  show(result, now = Date.now()) {
    this.result = result;
    this.failed = false;
    for (const s of this.data.sockets) this.paintTile(s.id, result ? result.states.get(s.id) : { state: null });
    this.render(now);
  }

  fail(now = Date.now()) {
    this.failed = true;
    if (!this.result) for (const s of this.data.sockets) this.paintTile(s.id, { state: null });
    this.render(now);
  }

  /** Re-renders the answer; cheap, also called every 30 s so the age and staleness move. */
  render(now = Date.now()) {
    const { result } = this;
    if (!result) {
      this.setText(this.el.eyebrow, 'Şu an');
      this.setText(this.el.big, this.failed ? 'Canlı durum alınamadı' : 'Henüz okuma yok');
      this.setText(this.el.sub, this.failed
        ? 'Bağlantı ya da veri kaynağı yanıt vermedi. Aşağıdaki istatistikler yine geçerli.'
        : 'Bugün ve dün için kayıtlı okuma bulunamadı.');
      this.setText(this.el.age, '');
      this.banner(this.failed ? 'error' : null);
      return;
    }
    const age = now - result.at;
    const stale = age > STALE_MS;
    const ac = this.data.sockets.filter((s) => s.kind === 'AC');
    const dc = this.data.sockets.filter((s) => s.kind === 'DC');
    const stateOf = (s) => result.states.get(s.id)?.state;
    const free = ac.filter((s) => stateOf(s) === 'available').length;
    const missing = ac.filter((s) => stateOf(s) == null).length;
    this.setText(this.el.eyebrow, stale ? 'Son bilinen durum' : 'Şu an · AC prizler');
    const verb = stale ? (free ? 'priz boştu' : 'Boş priz yoktu') : (free ? 'priz boş' : 'Boş priz yok');
    if (free) {
      this.el.big.replaceChildren(h('span', { class: 'num', text: String(free) }), ` ${verb}`);
    } else {
      this.setText(this.el.big, verb);
    }
    this.el.big.dataset.free = free ? 'yes' : 'no';
    const parts = [`${ac.length} AC prizden`];
    if (missing) parts.push(`${missing} prizde okuma yok`);
    if (dc.length) parts.push(`DC: ${dc.filter((s) => stateOf(s) === 'available').length}/${dc.length} boş`);
    this.setText(this.el.sub, parts.join(' · '));
    this.setText(this.el.age, `Son okuma ${clock(result.at)} · ${ago(age)}`);
    this.el.age.dataset.stale = stale ? 'yes' : 'no';
    this.banner(stale ? 'stale' : this.failed ? 'retry' : null, age);
    for (const s of this.data.sockets) {
      const entry = result.states.get(s.id);
      if (entry?.since) this.paintTile(s.id, entry, stale ? result.at : now, stale);
    }
    for (const { count, sockets } of this.groupCounts ?? []) {
      const known = sockets.filter((s) => stateOf(s) != null);
      count.textContent = known.length ? `${sockets.filter((s) => stateOf(s) === 'available').length}/${sockets.length} boş` : '';
    }
  }

  banner(kind, age = 0) {
    const el = this.el.banner;
    if (!kind) {
      el.hidden = true;
      return;
    }
    el.hidden = false;
    el.dataset.kind = kind;
    const retry = h('button', { type: 'button', class: 'link-button', text: 'Tekrar dene', onclick: () => this.onRetry?.() });
    const text = {
      stale: `Bu okuma ${ago(age)} yapıldı; şu anki durum farklı olabilir. Kayıt bir süredir gelmiyor.`,
      retry: 'Son yenileme başarısız oldu; gösterilen durum bir önceki okuma.',
      error: 'Canlı durum şu an okunamıyor.',
    }[kind];
    el.replaceChildren(...[icon('warn'), h('span', { text }), kind === 'stale' ? null : retry].filter(Boolean));
  }

  setText(el, text) {
    if (el.textContent !== text) el.textContent = text;
  }
}
