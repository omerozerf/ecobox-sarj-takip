// "DC hızlı şarj": when the fast chargers are free, from data.json `dc`. A "Hafta içi / Hafta
// sonu" switch picks the pooled day type (today's by default); one bar per hour, 06–23 unless
// "Tüm saatler" is on, as in the popular-times chart above. Under it the engine's sentence on
// the easiest and hardest hours of that day type leads; stay time and the other DC findings
// wait behind "N bulgu daha". The record's age is said once, in "Ne zaman", not here again.

import { empty, h, put, readout, roving } from './dom.js';
import { decimal, hourLabel, localSlot, minutes } from './format.js';
import { cellModel, range } from './heatmap.js';
import { findingLines, moreFindings } from './sections.js';

const DAY_TYPES = [
  { key: 'weekday', label: 'Hafta içi', hours: 'weekday_hours', finding: 'free_hours_Hafta içi' },
  { key: 'weekend', label: 'Hafta sonu', hours: 'weekend_hours', finding: 'free_hours_Hafta sonu' },
];
const DAY_HOURS = range(6, 23);
const ALL_HOURS = range(0, 23);

/** The lead: the day type's free-hours finding, or what the engine still waits for. */
function leadLine(dc, type) {
  const finding = dc.findings.find((f) => f.key === type.finding);
  if (finding) return findingLines(finding, 'dc-line');
  const missing = dc.missing.find((m) => m.key === type.finding) ?? dc.missing.find((m) => m.key === 'free_hours');
  return missing ? h('p', { class: 'dc-line quiet', text: missing.text }) : null;
}

/** Everything else the engine says about DC; stay time first, told plainly while it waits. */
function furtherFindings(dc) {
  const rest = dc.findings.filter((f) => !f.key.startsWith('free_hours'));
  const d = dc.durations;
  if (!rest.some((f) => f.key === 'occupancy') && d.median_min != null) {
    rest.unshift({ key: 'occupancy', text: `Şimdiye kadar ${d.count} şarj görüldü; araçlar genelde ${minutes(d.median_min)} kaldı.`, basis: `${d.count} şarj` });
  }
  return rest.sort((a, b) => (b.key === 'occupancy') - (a.key === 'occupancy'));
}

/** Fills the DC section, or leaves it hidden when the scope has no DC connector. */
export function renderDc(root, data, now = Date.now()) {
  const dc = data.dc;
  if (!dc) return false;
  root.hidden = false;
  const model = cellModel({ ...data, ...dc });
  const slot = localSlot(now);
  const today = slot.weekday >= 5 ? 'weekend' : 'weekday';
  const el = Object.fromEntries(['intro', 'days', 'chart', 'readout', 'facts', 'more', 'all']
    .map((name) => [name, root.querySelector(`[data-dc=${name}]`)]));
  const say = (head, detail) => readout(el.readout, head, detail);

  put(el.intro, `${dc.sockets.length} hızlı şarj prizi${dc.power_kw ? `, ${decimal(dc.power_kw)} kW` : ''}. Anlık durum yukarıda.`);
  put(el.more, moreFindings(furtherFindings(dc)));

  let picked = today;
  let all = false;
  const buttons = DAY_TYPES.map((type) => h('button', {
    type: 'button', class: 'area-option', 'aria-pressed': String(type.key === picked), text: type.label,
    onclick: () => {
      if (picked === type.key) return;
      picked = type.key;
      buttons.forEach((b, i) => b.setAttribute('aria-pressed', String(DAY_TYPES[i].key === picked)));
      draw(true);
    },
  }));
  put(el.days, buttons);
  el.all.hidden = false;
  el.all.addEventListener('click', () => {
    all = !all;
    el.all.setAttribute('aria-pressed', String(all));
    draw(true);
  });

  function draw(animate) {
    const type = DAY_TYPES.find((t) => t.key === picked);
    const row = dc.heatmap[type.hours];
    put(el.facts, leadLine(dc, type));
    if (!row.some(Boolean)) {
      put(el.chart, empty(`${type.label} için henüz kayıt yok.`));
      say('');
      return;
    }
    const hours = all ? ALL_HOURS : DAY_HOURS;
    const isToday = picked === today;
    const columns = hours.map((hour) => {
      const cell = row[hour];
      const [head, detail] = model.parts(`${type.label} ${hourLabel(hour)}`, cell);
      const kind = !cell ? 'pt-empty' : model.dotted(cell) ? 'pt-early' : null;
      return h('button', {
        type: 'button',
        class: ['pt-col', kind, isToday && hour === slot.hour ? 'pt-now' : null].filter(Boolean).join(' '),
        'aria-label': `${head}. ${detail}`, 'data-head': head, 'data-detail': detail,
        vars: cell ? { '--p': cell.p } : null,
      }, h('span', { class: 'pt-bar' }));
    });
    const n = hours.length;
    const bars = h('div', {
      class: `pt-bars${all ? ' is-all' : ''}${animate ? ' grow' : ''}`, role: 'group',
      'aria-label': `${type.label}, saat saat boş DC priz bulma şansı`, vars: { '--n': n },
    }, columns);
    const axis = hours.map((hour) => h('span', { text: hour % 3 === 0 ? String(hour).padStart(2, '0') : '' }));
    put(el.chart, h('div', { class: 'pt-plot' }, bars, h('div', { class: 'pt-axis', 'aria-hidden': 'true', vars: { '--n': n } }, axis)));
    const nowIndex = isToday ? hours.indexOf(slot.hour) : -1;
    roving(columns, Math.max(0, nowIndex),
      (i, key) => ({ ArrowLeft: Math.max(0, i - 1), ArrowRight: Math.min(n - 1, i + 1), Home: 0, End: n - 1 })[key],
      (i) => {
        columns.forEach((c) => c.classList.remove('is-picked'));
        columns[i].classList.add('is-picked');
        say(columns[i].dataset.head, columns[i].dataset.detail);
      });
    if (nowIndex >= 0) say(`Genelde bu saatte: ${columns[nowIndex].dataset.head}`, columns[nowIndex].dataset.detail);
    else say('', 'Ayrıntı için bir çubuğa dokun.');
  }
  draw(true);
  return true;
}
