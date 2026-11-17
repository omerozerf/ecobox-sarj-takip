// "Ne zaman boş olur": the weekday × hour map and the pooled weekday/weekend strips.
// Weekdays are rows on a wide card; below ~520 px the CSS turns hours into rows instead
// (same buttons, placed by --d/--h), so the whole week fits a phone without scrolling.

import { h, put, roving } from './dom.js';
import { hourLabel, int, localSlot, pct } from './format.js';

const BINS = ['%0–20', '%20–40', '%40–60', '%60–80', '%80–100'];
const bin = (p) => Math.min(4, Math.floor(p * 5));

function describe(where, cell, minPolls) {
  if (!cell) return `${where} · kayıt yok`;
  const thin = cell.polls < minPolls ? ' · az okuma, kesin değil' : '';
  return `${where} · ${pct(cell.p)} · ${int(cell.polls)} okuma, ${cell.days} gün${thin}`;
}

function cellButton(cell, label, minPolls, vars) {
  const classes = ['hm-cell'];
  if (!cell) classes.push('is-empty');
  else {
    classes.push(`b${bin(cell.p)}`);
    if (cell.polls < minPolls) classes.push('is-thin');
  }
  return h('button', { type: 'button', class: classes.join(' '), 'aria-label': label, vars });
}

export function renderHeatmap(root, data, now = Date.now()) {
  const { cells, weekday_hours: weekdayHours, weekend_hours: weekendHours } = data.heatmap;
  const minPolls = data.thresholds.min_hour_polls;
  const days = data.weekday_labels;
  const slot = localSlot(now);
  const readout = root.querySelector('[data-hm=readout]');
  const say = (text) => { readout.textContent = text; };

  // ----- the map -----
  const grid = h('div', { class: 'hm-grid', role: 'group', 'aria-label': 'Gün ve saate göre boş priz şansı' });
  const buttons = [];
  for (let d = 0; d < 7; d += 1) {
    grid.append(h('span', { class: `hm-day${d === slot.weekday ? ' is-now' : ''}`, vars: { '--d': d }, text: days[d] }));
  }
  for (let hour = 0; hour < 24; hour += 1) {
    const major = hour % 3 === 0 ? ' is-major' : '';
    grid.append(h('span', { class: `hm-hour${major}${hour === slot.hour ? ' is-now' : ''}`, vars: { '--h': hour }, text: String(hour).padStart(2, '0') }));
  }
  const texts = [];
  for (let d = 0; d < 7; d += 1) {
    for (let hour = 0; hour < 24; hour += 1) {
      const cell = cells[d][hour];
      const text = describe(`${days[d]} ${hourLabel(hour)}`, cell, minPolls);
      const button = cellButton(cell, text, minPolls, { '--d': d, '--h': hour });
      if (d === slot.weekday && hour === slot.hour) button.classList.add('is-now');
      grid.append(button);
      buttons.push(button);
      texts.push(text);
    }
  }
  const nowIndex = slot.weekday * 24 + slot.hour;
  const clearPicked = () => root.querySelectorAll('.is-picked').forEach((b) => b.classList.remove('is-picked'));
  roving(buttons, nowIndex, (i, key) => {
    const tall = getComputedStyle(grid).getPropertyValue('--tall').trim() === '1';
    const d = Math.floor(i / 24);
    const hour = i % 24;
    const step = {
      ArrowLeft: tall ? [-1, 0] : [0, -1], ArrowRight: tall ? [1, 0] : [0, 1],
      ArrowUp: tall ? [0, -1] : [-1, 0], ArrowDown: tall ? [0, 1] : [1, 0],
      Home: [0, -hour], End: [0, 23 - hour],
    }[key];
    if (!step) return null;
    const nd = d + step[0];
    const nh = hour + step[1];
    return nd < 0 || nd > 6 || nh < 0 || nh > 23 ? i : nd * 24 + nh;
  }, (i) => {
    clearPicked();
    buttons[i].classList.add('is-picked');
    say((i === nowIndex ? 'Şu an: ' : '') + texts[i]);
  });
  say(`Şu an: ${texts[nowIndex]}`);

  // ----- the pooled strips -----
  const strips = [['Hafta içi', weekdayHours], ['Hafta sonu', weekendHours]].map(([name, row]) => {
    const isNowRow = (slot.weekday < 5) === (name === 'Hafta içi');
    const stripButtons = row.map((cell, hour) => {
      const button = cellButton(cell, describe(`${name} ${hourLabel(hour)}`, cell, minPolls), minPolls, null);
      if (isNowRow && hour === slot.hour) button.classList.add('is-now');
      return button;
    });
    roving(stripButtons, isNowRow ? slot.hour : 0, (i, key) => ({ ArrowLeft: Math.max(0, i - 1), ArrowRight: Math.min(23, i + 1), Home: 0, End: 23 })[key],
      (i) => {
        clearPicked();
        stripButtons[i].classList.add('is-picked');
        say(stripButtons[i].getAttribute('aria-label'));
      });
    return h('div', { class: 'strip-row' },
      h('span', { class: 'strip-name', text: name }),
      h('div', { class: 'strip', role: 'group', 'aria-label': `${name}, saat saat` }, stripButtons));
  });

  put(root.querySelector('[data-hm=map]'), grid);
  put(root.querySelector('[data-hm=strips]'), strips,
    h('div', { class: 'strip-axis', 'aria-hidden': 'true' }, ['00', '06', '12', '18', '24'].map((t) => h('span', { text: t }))));
  put(root.querySelector('[data-hm=legend]'),
    BINS.map((text, i) => h('li', {}, h('span', { class: `swatch b${i}` }), text)),
    h('li', {}, h('span', { class: 'swatch b3 is-thin' }), `az okuma (<${minPolls})`),
    h('li', {}, h('span', { class: 'swatch is-empty' }), 'kayıt yok'));
}
