// The history sections below the live answer. The engine's findings are not a section of
// their own: each one leads (or follows) the section whose chart it talks about, so the page
// never says a thing twice. Every renderer takes its slice of data.json and fills its own
// container; a finding the map does not know lands in "Diğer bulgular", never nowhere.

import { empty, h, put, svg } from './dom.js';
import { decimal, hourLabel, int, minutes, pct, stamp } from './format.js';

const SECTION_OF = {
  week_change: 'when',
  socket_ranking: 'sockets',
  turnover: 'sockets',
  long_occupancy: 'sockets',
  reliability: 'sockets',
  occupancy: 'stay',
  occupancy_by_arrival: 'stay',
  arrivals: 'stay',
  sites: 'sites',
};

/** Findings per section, in the engine's order: {when, sockets, stay, sites, other}. */
export function findingsBySection(findings) {
  const out = { when: [], sockets: [], stay: [], sites: [], other: [] };
  for (const f of findings) out[f.key.startsWith('free_hours') ? 'when' : SECTION_OF[f.key] ?? 'other'].push(f);
  return out;
}

const findingLines = (f, textClass) => [h('p', { class: textClass, text: f.text }), h('p', { class: 'quiet basis', text: f.basis })];

/** The first finding leads the section; the rest are a compact list under its chart. */
export function renderFindingSlots(root, findings) {
  const lead = root.querySelector('[data-findings=lead]');
  const rest = root.querySelector('[data-findings=rest]');
  if (lead) put(lead, findings.length ? findingLines(findings[0], 'lead') : null);
  if (rest) {
    const others = findings.slice(lead ? 1 : 0);
    put(rest, others.length ? h('ul', { class: 'notes' }, others.map((f) => h('li', {}, findingLines(f, 'note')))) : null);
  }
}

export function renderOtherFindings(card, findings, missing) {
  const other = card.querySelector('[data-section=other]');
  card.querySelector('[data-part=other]').hidden = !findings.length;
  put(other, findings.length ? h('ul', { class: 'notes' }, findings.map((f) => h('li', {}, findingLines(f, 'note')))) : null);
  card.querySelector('[data-part=missing]').hidden = !missing.length;
  put(card.querySelector('[data-section=missing]'), h('ul', { class: 'missing-list' }, missing.map((m) => h('li', { text: m.text }))));
  card.hidden = !findings.length && !missing.length;
}

export function renderRanking(root, data) {
  const min = data.thresholds.min_socket_samples;
  if (!data.ranking.length) {
    put(root, empty('Henüz hiçbir prizde ölçüm yok.'));
    return;
  }
  const detailOf = (r) => `${int(r.samples)} ölçüm · ${decimal(r.observed_days)} gün kayıt`;
  const details = new Set(data.ranking.map(detailOf));
  const shared = details.size === 1 && data.ranking.every((r) => r.samples >= min) ? [...details][0] : null;
  const rows = data.ranking.map((r) => {
    const ranked = r.samples >= min;
    const detail = shared ? null : detailOf(r);
    return h('li', { class: `rank${ranked ? '' : ' is-unranked'}` },
      h('span', { class: 'rank-name', text: r.name }),
      ranked ? h('div', { class: 'bar', 'aria-hidden': 'true' }, h('span', { vars: { '--v': r.free_share } })) : null,
      h('span', { class: 'rank-value' }, ranked ? pct(r.free_share) : 'henüz sıralanmadı'),
      detail ? h('p', { class: 'quiet rank-detail', text: ranked ? detail : `${detail} · sıralama için ${int(min)} ölçüm gerekiyor` }) : null);
  });
  put(root, h('ol', { class: 'ranking', 'aria-label': 'Prizler, en sık boş olandan başlayarak' }, rows),
    shared ? h('p', { class: 'quiet ranking-basis', text: `Her priz için ${shared}.` }) : null);
}

export function renderStay(root, data, findings) {
  const d = data.durations;
  const has = (key) => findings.some((f) => f.key === key);
  const parts = [];
  if (d.median_min == null) {
    parts.push(empty('Prizde kalma süresi için henüz araç yok.'));
  } else {
    if (!has('occupancy')) { // the engine is still waiting: say plainly what little there is
      parts.push(h('p', { class: 'lead', text: `Şimdiye kadar ${d.count} araç görüldü; genelde ${minutes(d.median_min)} kaldılar. Sağlam bir sonuç için en az ${data.thresholds.min_sessions} araç gerekiyor.` }));
    }
    parts.push(quartileBar(d));
  }
  parts.push(h('h3', { text: 'Saate göre takma ve çıkarma' }), arrivalsChart(data.plug_in, data.unplug));
  if (!has('arrivals')) {
    const excluded = data.plug_in.excluded + data.unplug.excluded;
    const notes = [`${int(data.plug_in.count)} takma, ${int(data.unplug.count)} çıkarma`];
    if (excluded) notes.push(`saati tam bilinmeyen ${int(excluded)} takma ya da çıkarma sayılmadı`);
    parts.push(h('p', { class: 'quiet', text: `${notes.join(' · ')}.` }));
  }
  put(root, parts);
}

function quartileBar(d) {
  const top = Math.max(d.q3_min ?? d.median_min, d.median_min);
  const hours = Math.max(4, Math.ceil(top / 60) + 1);
  const at = (m) => `${(100 * m) / (hours * 60)}%`;
  const box = d.q1_min != null
    ? h('span', { class: 'q-box', vars: { '--from': at(d.q1_min), '--to': at(d.q3_min) } })
    : null;
  const step = hours > 8 ? 4 : 2;
  const ticks = [];
  for (let hr = 0; hr <= hours; hr += step) ticks.push(h('span', { vars: { '--at': at(hr * 60) }, text: hr ? `${hr} sa` : '0' }));
  return h('figure', { class: 'quartile' },
    h('div', { class: 'q-track', role: 'img', 'aria-label': d.q1_min != null
      ? `Araçların yarısı ${minutes(d.q1_min)} ile ${minutes(d.q3_min)} arası, genelde ${minutes(d.median_min)}`
      : `Genelde ${minutes(d.median_min)}` },
    box, h('span', { class: 'q-median', vars: { '--at': at(d.median_min) } })),
    h('div', { class: 'q-axis', 'aria-hidden': 'true' }, ticks),
    h('figcaption', { class: 'quiet legend-line' },
      d.q1_min != null ? h('span', { class: 'key-item' }, h('span', { class: 'key key-box' }), 'araçların yarısı') : null,
      h('span', { class: 'key-item' }, h('span', { class: 'key key-median' }), 'genelde')));
}

function arrivalsChart(plug, unplug) {
  const width = 480;
  const height = 150;
  const left = 8;
  const base = 120;
  const max = Math.max(1, ...plug.by_hour, ...unplug.by_hour);
  const slot = (width - left * 2) / 24;
  const bar = slot * 0.36;
  const bars = [];
  for (let hour = 0; hour < 24; hour += 1) {
    const x = left + hour * slot + slot * 0.12;
    for (const [i, dist] of [plug, unplug].entries()) {
      const value = dist.by_hour[hour];
      if (!value) continue;
      const hgt = (value / max) * (base - 12);
      bars.push(svg('rect', { class: i ? 'unplug' : 'plug', x: x + i * bar, y: base - hgt, width: bar - 1, height: hgt, rx: 1.5 }));
    }
  }
  const labels = [0, 6, 12, 18].map((hour) => svg('text', { x: left + hour * slot, y: base + 22, text: hourLabel(hour).slice(0, 2) }));
  const peak = (dist) => dist.by_hour.indexOf(Math.max(...dist.by_hour));
  const summary = plug.count + unplug.count
    ? `Saate göre takma ve çıkarma. En çok takma ${hourLabel(peak(plug))} saatinde, en çok çıkarma ${hourLabel(peak(unplug))} saatinde.`
    : 'Henüz takma ya da çıkarma yok.';
  return h('figure', { class: 'arrivals' },
    svg('svg', { viewBox: `0 0 ${width} ${height}`, role: 'img', 'aria-label': summary },
      svg('line', { class: 'axis', x1: left, x2: width - left, y1: base + 0.5, y2: base + 0.5 }),
      bars, labels, svg('text', { x: width - left, y: 12, 'text-anchor': 'end', class: 'max', text: `en çok ${max}` })),
    h('figcaption', { class: 'quiet legend-line' },
      h('span', { class: 'key-item' }, h('span', { class: 'key key-plug' }), 'takma'),
      h('span', { class: 'key-item' }, h('span', { class: 'key key-unplug' }), 'çıkarma')));
}

export function renderSites(root, data) {
  const sites = data.sites;
  if (!sites) {
    put(root, empty('Diğer Ecobox siteleri henüz kaydedilmedi; karşılaştırma onlar kaydedilince gelecek.'));
    return;
  }
  const parts = [];
  const compare = [];
  if (sites.home) compare.push(compareBar(sites.home.name, sites.home.busy_share, true));
  if (sites.overall != null) compare.push(compareBar(`Diğer ${int(sites.others)} site`, sites.overall, false));
  parts.push(h('div', { class: 'compare' }, compare));
  parts.push(h('p', { class: 'quiet', text: sites.overall == null
    ? 'Diğer sitelerle karşılaştırma için henüz yeterli ortak kayıt yok.'
    : 'Çubuk: prizlerin dolu olduğu zaman payı.' }));

  const min = data.thresholds.min_site_sockets;
  const loads = sites.loads.filter((l) => l.home || l.sockets >= min);
  if (!loads.length) {
    put(root, parts);
    return;
  }
  const rows = loads.map((l, i) => h('li', { class: `site${l.home ? ' is-home' : ''}` },
    h('span', { class: 'site-rank', text: String(i + 1) }),
    h('span', { class: 'site-name' }, l.name, h('span', { class: 'quiet', text: ` · ${l.sockets} priz` })),
    h('span', { class: 'site-value', text: pct(l.busy_share) }),
    h('div', { class: 'bar bar-busy', 'aria-hidden': 'true' }, h('span', { vars: { '--v': l.busy_share } }))));
  const n = loads.length;
  const list = h('div', { id: 'site-list', hidden: true },
    h('p', { class: 'quiet sites-caption', text: `En az ${min} AC prizli siteler, en dolu olandan` }),
    h('ol', { class: 'sites', 'aria-label': 'Siteler, en dolu olandan başlayarak' }, rows));
  const button = h('button', { type: 'button', class: 'more', 'aria-expanded': 'false', 'aria-controls': 'site-list' });
  const label = (open) => { button.textContent = open ? 'Listeyi gizle' : `${min}+ prizli ${n} siteyi göster`; };
  button.addEventListener('click', () => {
    const open = list.hidden;
    list.hidden = !open;
    button.setAttribute('aria-expanded', String(open));
    label(open);
  });
  label(false);
  parts.push(button, list);
  put(root, parts);
}

function compareBar(name, share, home) {
  return h('div', { class: `compare-row${home ? ' is-home' : ''}` },
    h('span', { class: 'compare-name', text: name }),
    h('span', { class: 'compare-value', text: `${pct(share)} dolu` }),
    h('div', { class: 'bar bar-busy', 'aria-hidden': 'true' }, h('span', { vars: { '--v': share } })));
}

export function renderFooter(root, data) {
  const c = data.coverage;
  const lines = [];
  lines.push(c.first
    ? `Kayıt: ${stamp(Date.parse(c.first))} – ${stamp(Date.parse(c.last))} · ${decimal(c.observed_days)} günlük gözlem, ${c.dates} farklı gün · ${c.ac_sockets} AC priz.`
    : 'Henüz kayıt yok.');
  const weeks = Math.round((Date.parse(data.window.until) - Date.parse(data.window.since)) / (7 * 86_400_000));
  lines.push(`İstatistikler ${stamp(Date.parse(data.generated_at))} itibarıyla, son ${weeks} haftanın kaydından hesaplandı. Saatler Türkiye saatidir.`);
  put(root, lines.map((text) => h('p', { text })));
}
