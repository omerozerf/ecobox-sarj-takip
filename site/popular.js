// "Ne zaman boş olur": a popular-times chart in the manner of a map app's place page. Day chips,
// one bar per hour (07–20, or every hour with "Tüm saatler"), bar height = chance that at least
// one AC socket is free in that weekday and hour. On today's chart the current hour is marked
// and one sentence above says how "now" compares.
//
// The comparison is made like for like: the bar is the chance that at least one socket is
// free, so "now" is compared as "is at least one free now". data.json has no per-hour mean free
// share, which comparing the number of free sockets would need.

import { empty, h, put, roving } from './dom.js';
import { hourLabel, localSlot, possessive } from './format.js';
import { cellModel, hoursFor, renderBadge } from './heatmap.js';

/** None free now while the hour usually has a free socket at least this often: "daha dolu". */
const USUALLY_FREE = 0.7;
/** Something free now while the hour usually has one less often than this: "daha boş". */
const USUALLY_FULL = 0.5;

/** How now compares with the hour's record; null when there is nothing fair to say. */
export function comparison(live, cell, model) {
  if (!live || live.stale || !live.reading || !cell || model.early(cell)) return null;
  if (live.free > 0 && cell.p < USUALLY_FULL) return 'genelde olduğundan daha boş';
  if (live.free === 0 && cell.p >= USUALLY_FREE) return 'genelde olduğundan daha dolu';
  return 'her zamanki gibi';
}

const nowShare = (live) => (live.free
  ? `${live.reading} prizden ${live.free}${possessive(live.free)} boş`
  : `${live.reading} prizin hiçbiri boş değil`);

/**
 * Fills the chart and returns {live({status, failed, settled}, now)}: called on every live
 * render (refresh and the 30 s tick). It only repaints the marker and the sentence, unless the
 * hour has turned, so a tick never replays the bars or drops the picked bar.
 */
export function renderPopular(root, data, { now = Date.now(), onHours } = {}) {
  const model = cellModel(data);
  const days = data.weekday_labels;
  const { cells } = data.heatmap;
  const hasData = cells.map((row) => row.some(Boolean));
  const el = {
    now: root.querySelector('[data-pt=now]'),
    days: root.querySelector('[data-pt=days]'),
    chart: root.querySelector('[data-pt=chart]'),
    readout: root.querySelector('[data-pt=readout]'),
    legend: root.querySelector('[data-pt=legend]'),
    toggle: root.querySelector('[data-pt=all-hours]'),
    badge: root.querySelector('[data-pt=badge]'),
  };
  const say = (text) => { el.readout.textContent = text; };
  let slot = localSlot(now);
  let all = false;
  let state = { status: null, failed: false, settled: false };
  renderBadge(el.badge, data);

  if (!hasData.some(Boolean)) {
    put(el.days);
    put(el.chart, empty('Henüz ölçüm yok; çubuklar ilk günlerin kaydıyla gelecek.'));
    say('');
    return { live() {} };
  }

  /** Today when it has data, otherwise the next day that has. */
  const fallbackDay = (from) => {
    for (let k = 0; k < 7; k += 1) if (hasData[(from + k) % 7]) return (from + k) % 7;
    return from;
  };
  let day = fallbackDay(slot.weekday);

  // ----- day chips -----
  const chips = days.map((label, d) => h('button', {
    type: 'button', class: 'pt-day', 'aria-pressed': 'false', disabled: !hasData[d],
    'aria-label': `${label}${!hasData[d] ? ', kayıt yok' : ''}`,
    onclick: () => { day = d; paintChips(); draw(true); },
  }, h('span', { text: label })));
  const paintChips = () => chips.forEach((chip, d) => {
    chip.setAttribute('aria-pressed', String(d === day));
    chip.classList.toggle('is-today', d === slot.weekday);
    chip.setAttribute('aria-label', `${days[d]}${d === slot.weekday ? ', bugün' : ''}${!hasData[d] ? ', kayıt yok' : ''}`);
  });
  put(el.days, chips);
  paintChips();

  // ----- the bars -----
  let columns = [];
  function draw(animate) {
    const hours = hoursFor(all);
    const today = day === slot.weekday;
    columns = hours.map((hour) => {
      const cell = cells[day][hour];
      const label = model.describe(`${days[day]} ${hourLabel(hour)}`, cell);
      const kind = !cell ? 'pt-empty' : model.dotted(cell) ? 'pt-early' : null;
      return h('button', {
        type: 'button',
        class: ['pt-col', kind, today && hour === slot.hour ? 'pt-now' : null].filter(Boolean).join(' '),
        'aria-label': label, 'data-label': label, 'data-hour': hour,
        vars: cell ? { '--p': cell.p } : null,
      }, h('span', { class: 'pt-bar' }));
    });
    const axis = hours.map((hour) => h('span', { text: hour % 3 === 0 ? String(hour).padStart(2, '0') : '' }));
    const bars = h('div', {
      class: `pt-bars${animate ? ' grow' : ''}${all ? ' is-all' : ''}`, role: 'group',
      'aria-label': `${days[day]}, saat saat boş priz bulma şansı`, vars: { '--n': hours.length },
    }, columns);
    put(el.chart, h('div', { class: 'pt-plot' }, bars, h('div', { class: 'pt-axis', 'aria-hidden': 'true', vars: { '--n': hours.length } }, axis)));

    const nowIndex = today ? hours.indexOf(slot.hour) : -1;
    roving(columns, Math.max(0, nowIndex),
      (i, key) => ({ ArrowLeft: Math.max(0, i - 1), ArrowRight: Math.min(columns.length - 1, i + 1), Home: 0, End: columns.length - 1 })[key],
      (i) => {
        columns.forEach((c) => c.classList.remove('is-picked'));
        columns[i].classList.add('is-picked');
        say(columns[i].getAttribute('aria-label'));
      });
    if (nowIndex >= 0) say(`Genelde bu saatte: ${columns[nowIndex].dataset.label}`);
    else say('Ayrıntı için bir çubuğa dokun.');
    paintLive();
  }

  // ----- the live reading: one sentence above the chart -----
  // No mark on the bar: a bar is the chance that at least one socket is free, the live reading
  // is a count of free sockets, and drawing both on one scale read as the same measure.
  function paintLive() {
    const { status, failed, settled } = state;
    const usable = status && !status.stale && status.reading > 0;
    const column = columns.find((c) => c.classList.contains('pt-now'));
    if (column) {
      column.setAttribute('aria-label',
        usable ? `${column.dataset.label} · şu an ${nowShare(status)}` : column.dataset.label);
    }

    let text;
    if (!status) {
      text = failed ? 'canlı durum alınamadı; çubuklar geçmiş kayıttan.'
        : settled ? 'bugün için canlı ölçüm yok.' : 'canlı durum yükleniyor…';
    } else if (status.stale) {
      text = 'son ölçüm eski, şu anki durum bilinmiyor.';
    } else if (!status.reading) {
      text = 'AC prizlerden ölçüm yok.';
    } else {
      const compared = comparison(status, cells[slot.weekday][slot.hour], model);
      text = compared ? `${compared} · ${nowShare(status)}` : nowShare(status);
    }
    const key = `${text}|${usable}`;
    if (el.now.dataset.key === key) return; // unchanged: keep the live region quiet
    el.now.dataset.key = key;
    el.now.dataset.live = usable ? 'yes' : 'no';
    el.now.replaceChildren(h('span', { class: 'pt-live', text: 'Şu an' }), ` ${text}`);
  }

  el.toggle.hidden = false;
  el.toggle.addEventListener('click', () => {
    all = !all;
    el.toggle.setAttribute('aria-pressed', String(all));
    draw(true);
    onHours?.(all);
  });

  const anyEarly = cells.flat().some((c) => c && model.dotted(c));
  put(el.legend,
    h('li', {}, h('span', { class: 'pt-key' }), 'boş priz bulma şansı'),
    anyEarly ? h('li', {}, h('span', { class: 'pt-key pt-early' }), model.youngMap ? `az ölçüm (${model.minPolls} ölçümden az)` : 'erken veri') : null,
    h('li', {}, h('span', { class: 'pt-key pt-empty' }), 'kayıt yok'));

  draw(true);

  return {
    live(next, at = Date.now()) {
      state = next;
      const turned = localSlot(at);
      if (turned.weekday !== slot.weekday || turned.hour !== slot.hour) {
        const followToday = day === slot.weekday;
        slot = turned;
        if (followToday) day = fallbackDay(slot.weekday);
        paintChips();
        draw(false);
        return;
      }
      paintLive();
    },
  };
}
