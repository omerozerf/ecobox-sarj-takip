// "Şu an": the big answer, the free sockets named right under it, and one tile per socket
// grouped by location. Groups and tiles are built once from data.json; a live reading only
// repaints them, reorders the groups (most free first) and folds the ones with nothing free.

import { empty, h, put } from './dom.js';
import { ago, clock, decimal, minutes, price, titled } from './format.js';
import { chargePost, icon } from './icons.js';
import { acStatus } from './live.js';
const collator = new Intl.Collator('tr', { numeric: true });
const PROBLEM_STATES = new Set(['faulted', 'offline', 'maintenance', 'unavailable']);

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

/** Groups by free sockets, most first; ties keep their order; DC stays last. */
export function orderByFree(groups, freeOf) {
  return groups
    .map((g, index) => ({ g, index }))
    .sort((a, b) => (a.g.key === 'dc') - (b.g.key === 'dc') || freeOf(b.g) - freeOf(a.g) || a.index - b.index)
    .map(({ g }) => g);
}

/** "22 kW · 9,29 TL/kWh"; each part kept on one line. */
function socketMeta(socket) {
  return [socket.power_kw ? `${decimal(socket.power_kw)} kW` : null, price(socket.price)]
    .filter(Boolean).map((part) => part.replaceAll(' ', ' ')).join(' · ') || 'güç ve fiyat bilinmiyor';
}

const reducedMotion = () => matchMedia('(prefers-reduced-motion: reduce)').matches;

export class LiveView {
  constructor(root, data) {
    this.data = data;
    this.labels = { ...data.state_labels, charging: 'dolu', unknown: 'bilinmiyor', none: 'ölçüm yok', loading: 'yükleniyor' };
    this.result = null;
    this.failed = false;
    this.el = {
      eyebrow: root.querySelector('[data-live=eyebrow]'),
      big: root.querySelector('[data-live=big]'),
      free: root.querySelector('[data-live=free]'),
      sub: root.querySelector('[data-live=sub]'),
      age: root.querySelector('[data-live=age]'),
      banner: root.querySelector('[data-live=banner]'),
      groups: root.querySelector('[data-live=tiles]'),
      art: root.querySelector('[data-live=art]'),
    };
    this.root = root;
    this.shownFree = null; // the count on screen, so only a change crossfades
    this.onChange = null; // called after every render (the chart follows the live reading)
    this.settled = false; // the first fetch has answered, one way or the other
    this.el.art.replaceChildren(chargePost());
    this.tiles = new Map();
    this.groups = socketGroups(data.sockets).map((g, i) => this.buildGroup(g, i));
    if (this.groups.length) put(this.el.groups, this.groups.map((g) => g.node));
    else put(this.el.groups, empty('Bu kapsamda priz yok.'));
  }

  buildGroup(group, index) {
    const metas = new Set(group.sockets.map(socketMeta));
    const shared = metas.size === 1 ? [...metas][0] : null; // said once for the whole group
    const summary = h('span', { class: 'group-summary' });
    const bodyId = `group-${index}`;
    const toggle = h('button', { type: 'button', class: 'group-head', 'aria-expanded': 'true', 'aria-controls': bodyId },
      icon(group.key === 'dc' ? 'dc' : 'ac'), h('span', { class: 'group-name', text: group.label }), summary, icon('chevron'));
    const body = h('div', { class: 'group-body', id: bodyId },
      shared ? h('p', { class: 'group-meta', text: shared }) : null,
      h('ul', { class: 'tiles' }, group.sockets.map((s) => this.tile(s, shared == null))));
    const node = h('section', { class: 'group', 'aria-label': group.label, 'data-open': 'yes' }, toggle, body);
    const entry = { ...group, node, toggle, body, summary, touched: false };
    toggle.addEventListener('click', () => {
      entry.touched = true;
      this.setOpen(entry, toggle.getAttribute('aria-expanded') !== 'true');
    });
    return entry;
  }

  setOpen(group, open) {
    group.toggle.setAttribute('aria-expanded', String(open));
    group.body.hidden = !open;
    group.node.dataset.open = open ? 'yes' : 'no';
  }

  tile(socket, withMeta) {
    const state = h('span', { class: 'tile-state' });
    const node = h('li', { class: 'tile', 'data-state': 'loading', id: `tile-${socket.id}`, tabindex: '-1' },
      h('span', { class: 'tile-name', text: socket.name }),
      state,
      withMeta ? h('span', { class: 'tile-meta', text: socketMeta(socket) }) : null);
    this.tiles.set(socket.id, { node, state });
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
    let text = this.labels[state] ?? state;
    if (entry?.since) {
      const span = minutes(Math.max(60_000, until - entry.since.start) / 60_000);
      text += entry.since.exact && !stale ? ` · ${span}` : ` · en az ${span}`;
    }
    const name = state === 'available' ? 'free' : state === 'charging' ? 'busy'
      : PROBLEM_STATES.has(state) ? 'fault' : state === 'loading' ? null : 'unknown';
    tile.state.replaceChildren(...[name ? icon(name) : null, document.createTextNode(text)].filter(Boolean));
  }

  loading() {
    this.setText(this.el.eyebrow, 'Şu an');
    this.setText(this.el.big, 'Yükleniyor…');
  }

  show(result, now = Date.now()) {
    if (!this.settled) this.root.classList.add('rise');
    this.settled = true;
    this.result = result;
    this.failed = false;
    for (const s of this.data.sockets) this.paintTile(s.id, result ? result.states.get(s.id) : { state: null });
    this.render(now);
  }

  fail(now = Date.now()) {
    if (!this.settled) this.root.classList.add('rise');
    this.settled = true;
    this.failed = true;
    if (!this.result) for (const s of this.data.sockets) this.paintTile(s.id, { state: null });
    this.render(now);
  }

  /** What the chart needs from the live side: the AC figure, or null without a reading. */
  status(now = Date.now()) {
    return acStatus(this.data.sockets, this.result, now);
  }

  stateOf(socket) {
    return this.result?.states.get(socket.id)?.state;
  }

  /** Re-renders the answer; cheap, also called every 30 s so the age and staleness move. */
  render(now = Date.now()) {
    this.renderAnswer(now);
    this.onChange?.();
  }

  renderAnswer(now) {
    const { result } = this;
    this.el.art.hidden = !this.settled || Boolean(result); // only the empty and error states
    if (!result) {
      this.setText(this.el.eyebrow, 'Şu an');
      this.setText(this.el.big, this.failed ? 'Canlı durum alınamadı' : 'Henüz ölçüm yok');
      this.el.big.dataset.free = 'unknown';
      this.setText(this.el.free, '');
      this.setText(this.el.sub, this.failed
        ? 'Bağlantı ya da veri kaynağı yanıt vermedi. Aşağıdaki istatistikler yine geçerli.'
        : 'Bugün ve dün için kayıtlı ölçüm bulunamadı.');
      this.setText(this.el.age, '');
      delete this.el.age.dataset.stale;
      this.shownFree = null;
      this.banner(this.failed ? 'error' : null);
      return;
    }
    const age = now - result.at;
    const status = acStatus(this.data.sockets, result, now);
    const { stale } = status;
    const ac = this.data.sockets.filter((s) => s.kind === 'AC');
    const dc = this.data.sockets.filter((s) => s.kind === 'DC');
    const free = ac.filter((s) => this.stateOf(s) === 'available');
    const missing = status.total - status.reading;

    this.setText(this.el.eyebrow, stale ? 'Son bilinen durum' : 'Şu an · AC prizler');
    const verb = stale ? (free.length ? 'priz boştu' : 'Boş priz yoktu') : (free.length ? 'priz boş' : 'Boş priz yok');
    const changed = this.shownFree != null && this.shownFree !== status.free;
    this.shownFree = status.free;
    if (free.length) this.el.big.replaceChildren(h('span', { class: 'num', text: String(free.length) }), ` ${verb}`);
    else this.setText(this.el.big, verb);
    if (changed) this.crossfade();
    this.el.big.dataset.free = free.length ? 'yes' : 'no';

    const ordered = orderByFree(this.groups, (g) => g.sockets.filter((s) => this.stateOf(s) === 'available').length);
    this.renderFreeLine(free, ordered, ac, stale);
    const parts = [`${ac.length} AC prizden`];
    if (missing) parts.push(`${missing} prizde ölçüm yok`);
    if (dc.length) parts.push(`DC: ${dc.filter((s) => this.stateOf(s) === 'available').length}/${dc.length} boş`);
    this.setText(this.el.sub, parts.join(' · '));
    this.setText(this.el.age, `Son ölçüm ${clock(result.at)} · ${ago(age)}`);
    this.el.age.dataset.stale = stale ? 'yes' : 'no';
    this.banner(stale ? 'stale' : this.failed ? 'retry' : null, age);

    for (const s of this.data.sockets) {
      const entry = result.states.get(s.id);
      if (entry?.since) this.paintTile(s.id, entry, stale ? result.at : now, stale);
    }
    this.renderGroups(ordered);
  }

  /** The big answer just changed on a refresh: a short fade instead of a jump. */
  crossfade() {
    if (reducedMotion()) return;
    this.el.big.animate([{ opacity: 0.15, filter: 'blur(2px)' }, { opacity: 1, filter: 'blur(0)' }],
      { duration: 220, easing: 'ease-out' });
  }

  renderFreeLine(free, ordered, ac, stale) {
    if (!free.length) {
      const allBusy = ac.length > 0 && ac.every((s) => this.stateOf(s) === 'charging');
      const text = stale ? 'Son ölçümde boş AC priz yoktu.'
        : allBusy ? 'Şu an bütün AC prizler dolu.' : 'Şu an boş AC priz yok.';
      this.setText(this.el.free, text);
      this.el.free.dataset.kind = 'none';
      return;
    }
    const key = `${free.map((s) => s.id).join()}|${stale}|${ordered.map((g) => g.key).join()}`;
    if (this.el.free.dataset.key === key) return; // unchanged: keep focus and the live region quiet
    const rank = new Map(ordered.flatMap((g, gi) => g.sockets.map((s, si) => [s.id, gi * 1000 + si])));
    const chips = [...free].sort((a, b) => rank.get(a.id) - rank.get(b.id)).map((s) => h('button', {
      type: 'button', class: 'free-chip', onclick: () => this.reveal(s.id),
    }, icon('free'), s.name));
    this.el.free.replaceChildren(h('span', { class: 'free-label', text: stale ? 'Son ölçümde boş:' : 'Boş:' }), ...chips);
    this.el.free.dataset.key = key;
    this.el.free.dataset.kind = 'some';
  }

  renderGroups(ordered) {
    const current = [...this.el.groups.children];
    if (ordered.some((g, i) => current[i] !== g.node)) this.el.groups.append(...ordered.map((g) => g.node));
    for (const g of ordered) {
      const states = g.sockets.map((s) => this.stateOf(s));
      const free = states.filter((s) => s === 'available').length;
      g.summary.textContent = this.summary(states, free);
      g.node.dataset.free = free ? 'yes' : 'no';
      if (!g.touched) this.setOpen(g, free > 0);
    }
  }

  /** "2/4 boş" while one is free; otherwise what the sockets are: "4/4 dolu", "3 dolu · 1 arızalı". */
  summary(states, free) {
    const n = states.length;
    if (free) return `${free}/${n} boş`;
    const counts = new Map();
    for (const s of states) {
      const label = this.labels[s ?? 'none'] ?? s;
      counts.set(label, (counts.get(label) ?? 0) + 1);
    }
    if (counts.size === 1) return `${n}/${n} ${[...counts.keys()][0]}`;
    return [...counts].map(([label, count]) => `${count} ${label}`).join(' · ');
  }

  /** A free chip's target: open its group, bring the tile into view and focus it. */
  reveal(id) {
    const group = this.groups.find((g) => g.sockets.some((s) => s.id === id));
    group.touched = true;
    this.setOpen(group, true);
    const tile = this.tiles.get(id).node;
    tile.scrollIntoView({ block: 'center', behavior: reducedMotion() ? 'auto' : 'smooth' });
    tile.focus({ preventScroll: true });
  }

  banner(kind, age = 0) {
    const el = this.el.banner;
    if (!kind) {
      el.hidden = true;
      return;
    }
    el.hidden = false;
    el.dataset.kind = kind;
    const text = {
      stale: `Bu ölçüm ${ago(age)} yapıldı; şu anki durum farklı olabilir. Kayıt bir süredir gelmiyor.`,
      retry: 'Son yenileme başarısız oldu; gösterilen durum bir önceki ölçüm.',
      error: 'Canlı durum şu an okunamıyor.',
    }[kind];
    const retry = kind === 'stale' ? null
      : h('button', { type: 'button', class: 'link-button', text: 'Tekrar dene', onclick: () => this.onRetry?.() });
    el.replaceChildren(...[icon(kind === 'stale' ? 'clock' : 'warn'), h('span', { text }), retry].filter(Boolean));
  }

  setText(el, text) {
    if (el.textContent !== text) el.textContent = text;
    delete el.dataset.key;
  }
}
