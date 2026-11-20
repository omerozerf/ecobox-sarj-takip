// "Haftalık görünüm", the secondary view under the popular-times chart: the weekday × hour map
// (office hours by default, every hour with the chart's toggle) and the pooled weekday/weekend
// strips, behind a disclosure button. On a wide card weekdays are rows; below ~520 px the CSS
// turns hours into rows instead (same buttons, placed by --d/--c), so the week fits a phone
// without scrolling sideways. `cellModel` and `renderBadge` are shared with the chart.

import { h, put, readout, roving } from './dom.js';
import { icon } from './icons.js';
import { decimal, hourLabel, int, localSlot, pct } from './format.js';

const BINS = ['%0–20', '%20–40', '%40–60', '%60–80', '%80–100'];
export const DAY_HOURS = { first: 7, last: 20 };
const SETTLED_DATES = 14;
const bin = (p) => Math.min(4, Math.floor(p * 5));
export const range = (first, last) => Array.from({ length: last - first + 1 }, (_, i) => first + i);
export const hoursFor = (all) => (all ? range(0, 23) : range(DAY_HOURS.first, DAY_HOURS.last));

/**
 * Shared by every cell: what counts as early (thin) data, how a cell is told, and whether it
 * carries the early-data dot. A cell is early with too few polls or too few dates. While at
 * least half of the map is early by dates, the day-based dots are left out (the badge says it
 * once for the whole map); a cell with too few polls keeps its dot in every case.
 */
export function cellModel(data) {
  const minPolls = data.thresholds.min_hour_polls;
  const minDates = data.thresholds.min_pattern_dates;
  const filled = data.heatmap.cells.flat().filter(Boolean);
  const youngMap = filled.length > 0 && filled.filter((c) => c.days < minDates).length * 2 >= filled.length;
  const early = (cell) => cell != null && (cell.polls < minPolls || cell.days < minDates);
  const dotted = (cell) => cell != null && (cell.polls < minPolls || (!youngMap && cell.days < minDates));
  /** [head, detail]: the readout sets the head in bold and the detail muted. */
  const parts = (where, cell) => {
    if (!cell) return [where, 'kayıt yok'];
    return [`${where}, şans ${pct(cell.p)}`, `${int(cell.polls)} ölçüm, ${cell.days} gün${early(cell) ? ', erken veri' : ''}`];
  };
  const describe = (where, cell) => parts(where, cell).join('. ');
  const classes = (cell) => (cell ? [`b${bin(cell.p)}`, dotted(cell) ? 'is-early' : ''] : ['is-empty']).filter(Boolean);
  const anyDot = filled.some(dotted) || [...data.heatmap.weekday_hours, ...data.heatmap.weekend_hours].some(dotted);
  return { early, dotted, parts, describe, classes, minPolls, minDates, youngMap, anyDot };
}

/**
 * Fills the weekly view and wires its disclosure button once. Returns {setAll(all), setArea(view)}:
 * the chart's "Tüm saatler" toggle also widens this map, and the area switch redraws it for the
 * other area (`view` is data.json with that area's figures).
 */
export function renderWeekly(root, initial, now = Date.now()) {
  let data = initial;
  let model = cellModel(data);
  const slot = localSlot(now);
  const readoutEl = root.querySelector('[data-hm=readout]');
  const say = ([head, detail]) => readout(readoutEl, head, detail);
  const clearPicked = () => root.querySelectorAll('.is-picked').forEach((b) => b.classList.remove('is-picked'));

  const open = root.querySelector('[data-hm=open]');
  const panel = root.querySelector('[data-hm=panel]');
  open.addEventListener('click', () => {
    const show = panel.hidden;
    panel.hidden = !show;
    open.setAttribute('aria-expanded', String(show));
  });

  let all = false;
  const drawMap = () => put(root.querySelector('[data-hm=map]'), mapGrid(data, model, slot, hoursFor(all), say, clearPicked));

  function drawAll() {
    drawMap();
    const nowCell = data.heatmap.cells[slot.weekday][slot.hour];
    const [nowHead, nowDetail] = model.parts(`${data.weekday_labels[slot.weekday]} ${hourLabel(slot.hour)}`, nowCell);
    say([`Genelde bu saatte: ${nowHead}`, nowDetail]);

    // ----- the pooled strips -----
    const strips = [['Hafta içi', data.heatmap.weekday_hours], ['Hafta sonu', data.heatmap.weekend_hours]].map(([name, row]) => {
      const isNowRow = (slot.weekday < 5) === (name === 'Hafta içi');
      const told = row.map((cell, hour) => model.parts(`${name} ${hourLabel(hour)}`, cell));
      const buttons = row.map((cell, hour) => {
        const button = h('button', { type: 'button', class: ['hm-cell', ...model.classes(cell)].join(' '), 'aria-label': told[hour].join('. ') });
        if (isNowRow && hour === slot.hour) button.classList.add('is-now');
        return button;
      });
      roving(buttons, isNowRow ? slot.hour : 0,
        (i, key) => ({ ArrowLeft: Math.max(0, i - 1), ArrowRight: Math.min(23, i + 1), Home: 0, End: 23 })[key],
        (i) => { clearPicked(); buttons[i].classList.add('is-picked'); say(told[i]); });
      return h('div', { class: 'strip-row' },
        h('span', { class: 'strip-name', text: name }),
        h('div', { class: 'strip', role: 'group', 'aria-label': `${name}, saat saat` }, buttons));
    });
    put(root.querySelector('[data-hm=strips]'), strips,
      h('div', { class: 'strip-axis', 'aria-hidden': 'true' }, ['00', '06', '12', '18', '24'].map((t) => h('span', { text: t }))));

    put(root.querySelector('[data-hm=legend]'),
      BINS.map((text, i) => h('li', {}, h('span', { class: `swatch b${i}` }), text)),
      !model.anyDot ? null : h('li', {}, h('span', { class: 'swatch b3 is-early' }), model.youngMap
        ? `az ölçüm (${model.minPolls} ölçümden az)`
        : `erken veri (${model.minDates} günden ya da ${model.minPolls} ölçümden az)`),
      h('li', {}, h('span', { class: 'swatch is-empty' }), 'kayıt yok'));
  }
  drawAll();

  return {
    setAll(value) {
      if (value === all) return;
      all = value;
      drawMap();
    },
    setArea(view) {
      data = view;
      model = cellModel(data);
      drawAll();
    },
  };
}

function mapGrid(data, model, slot, hours, say, clearPicked) {
  const days = data.weekday_labels;
  const grid = h('div', { class: 'hm-grid', role: 'group', 'aria-label': 'Gün ve saate göre boş priz şansı', vars: { '--cols': hours.length } });
  const step = hours.length > 14 ? 3 : 1;
  days.forEach((label, d) => grid.append(h('span', { class: `hm-day${d === slot.weekday ? ' is-now' : ''}`, vars: { '--d': d }, text: label })));
  hours.forEach((hour, c) => grid.append(h('span', {
    class: `hm-hour${hour % step === 0 ? ' is-major' : ''}${hour === slot.hour ? ' is-now' : ''}`,
    vars: { '--c': c }, text: String(hour).padStart(2, '0'),
  })));
  const buttons = [];
  const texts = [];
  for (let d = 0; d < 7; d += 1) {
    hours.forEach((hour, c) => {
      const cell = data.heatmap.cells[d][hour];
      const text = model.parts(`${days[d]} ${hourLabel(hour)}`, cell);
      const button = h('button', { type: 'button', class: ['hm-cell', ...model.classes(cell)].join(' '), 'aria-label': text.join('. '), vars: { '--d': d, '--c': c } });
      if (d === slot.weekday && hour === slot.hour) button.classList.add('is-now');
      grid.append(button);
      buttons.push(button);
      texts.push(text);
    });
  }
  const n = hours.length;
  const nowColumn = hours.indexOf(slot.hour);
  const start = slot.weekday * n + Math.max(0, nowColumn);
  roving(buttons, start, (i, key) => {
    const tall = getComputedStyle(grid).getPropertyValue('--tall').trim() === '1';
    const d = Math.floor(i / n);
    const c = i % n;
    const move = {
      ArrowLeft: tall ? [-1, 0] : [0, -1], ArrowRight: tall ? [1, 0] : [0, 1],
      ArrowUp: tall ? [0, -1] : [-1, 0], ArrowDown: tall ? [0, 1] : [1, 0],
      Home: [0, -c], End: [0, n - 1 - c],
    }[key];
    if (!move) return null;
    const nd = d + move[0];
    const nc = c + move[1];
    return nd < 0 || nd > 6 || nc < 0 || nc >= n ? i : nd * n + nc;
  }, (i) => {
    clearPicked();
    buttons[i].classList.add('is-picked');
    say(texts[i]);
  });
  return grid;
}

/** While the record is short: how many days the squares actually rest on. */
export function renderBadge(badge, data) {
  const cells = data.heatmap.cells.flat().filter(Boolean);
  if (data.coverage.dates >= SETTLED_DATES || !cells.length) {
    badge.hidden = true;
    return;
  }
  const single = cells.filter((c) => c.days === 1).length;
  const most = Math.max(...cells.map((c) => c.days));
  const rest = single * 2 > cells.length ? 'çoğu saat tek bir güne dayanıyor.' : `saatler en çok ${most} güne dayanıyor.`;
  badge.replaceChildren(icon('info'), h('span', { text: `Henüz ${decimal(data.coverage.observed_days)} günlük kayıt: ${rest}` }));
  badge.hidden = false;
}
