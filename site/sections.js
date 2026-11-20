// The history sections below the live answer. The engine's findings are not a section of
// their own: each one leads (or follows) the section whose chart it talks about, so the page
// never says a thing twice. Every renderer takes its slice of data.json and fills its own
// container; a finding the map does not know lands in "Diğer bulgular", never nowhere.
//
// The secondary area ("Arge tarafı") follows the primary one in each section in a quieter form:
// a small heading, its findings as lead sentences only (the rest behind one disclosure), and a
// compact version of the section's figure where one helps.

import { leadSentence } from './prose.js';
import { empty, h, prose, put, svg } from './dom.js';
import { decimal, hourLabel, int, minutes, pct, stamp } from './format.js';
import { icon } from './icons.js';

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

/** A finding as it reads best: one sentence per line, figures in bold, its basis small under it. */
export const findingLines = (f, textClass) => [h('p', { class: textClass }, prose(f.text)), h('p', { class: 'basis', text: f.basis })];

let disclosures = 0;

/**
 * Further findings behind one quiet row, "N bulgu daha", closed by default; open, the same
 * prose as the lead (one sentence per line, figures in bold, basis under each). null when
 * there are none.
 */
export function moreFindings(findings) {
  if (!findings.length) return null;
  disclosures += 1;
  const id = `more-${disclosures}`;
  const body = h('ul', { id, class: 'notes more-body', hidden: true },
    findings.map((f) => h('li', {}, findingLines(f, 'note'))));
  const button = h('button', { type: 'button', class: 'more-row', 'aria-expanded': 'false', 'aria-controls': id },
    h('span', { text: `${findings.length} bulgu daha` }), icon('chevron'));
  button.addEventListener('click', () => {
    const open = body.hidden;
    body.hidden = !open;
    button.setAttribute('aria-expanded', String(open));
  });
  return h('div', { class: 'more' }, button, body);
}

/**
 * The first finding leads the section in full; the rest wait behind "N bulgu daha" under its
 * chart. Without a lead slot the lead opens the rest slot.
 */
export function renderFindingSlots(root, findings) {
  const lead = root.querySelector('[data-findings=lead]');
  const rest = root.querySelector('[data-findings=rest]');
  if (lead) put(lead, findings.length ? findingLines(findings[0], 'lead') : null);
  if (rest) {
    const others = findings.slice(lead ? 1 : 0);
    put(rest, lead || !others.length ? moreFindings(others)
      : [h('div', { class: 'rest-lead' }, findingLines(others[0], 'lead')), moreFindings(others.slice(1))]);
  }
}

/**
 * The secondary area's findings, short: the lead finding's first sentence with a small "tümü"
 * button after it, which opens every finding in full with what it rests on. null without
 * findings.
 */
export function compactFindings(findings) {
  if (!findings.length) return null;
  const [first, rest] = leadSentence(findings[0].text);
  const lead = h('p', { class: 'brief' }, prose(first));
  if (findings.length === 1 && !rest && !findings[0].basis) return lead;
  disclosures += 1;
  const id = `more-${disclosures}`;
  const body = h('ul', { id, class: 'notes brief-more', hidden: true },
    findings.map((f) => h('li', {}, findingLines(f, 'note'))));
  const label = findings.length > 1 ? `${findings.length} bulgunun tamamı` : 'ayrıntı';
  const button = h('button', { type: 'button', class: 'more-link', 'aria-expanded': 'false', 'aria-controls': id },
    label, icon('chevron'));
  button.addEventListener('click', () => {
    const open = body.hidden;
    body.hidden = !open;
    button.setAttribute('aria-expanded', String(open));
  });
  lead.append(' ', button);
  return [lead, body];
}

/**
 * A quiet block for the secondary area, hidden when empty: a small heading, then the given
 * parts. When it starts with a short sentence the name runs in before it instead, a line less.
 */
export function renderSecondary(root, area, ...parts) {
  const filled = parts.flat(Infinity).filter(Boolean);
  root.hidden = !area || !filled.length;
  if (root.hidden) return;
  if (filled[0].classList.contains('brief')) {
    filled[0].prepend(h('strong', { class: 'run-in', text: area.label }), ' ');
    put(root, filled);
  } else {
    put(root, h('h3', { class: 'sub-area', text: area.label }), filled);
  }
}

/** Findings no section claims: a section of their own, shown only when there are some. */
export function renderOtherFindings(section, findings, secondary = null, secondaryFindings = []) {
  put(section.querySelector('[data-section=other]'),
    findings.length ? h('ul', { class: 'notes' }, findings.map((f) => h('li', {}, findingLines(f, 'note')))) : null);
  renderSecondary(section.querySelector('[data-section=other-secondary]'), secondary, compactFindings(secondaryFindings));
  section.hidden = !findings.length && !secondaryFindings.length;
}

/**
 * What the record is still too short for: one quiet line (or a short list) above the footer.
 * A line the secondary area shares word for word is said once; its own lines carry its name.
 */
export function renderMissing(root, primary, secondary = null) {
  const said = new Set(primary.map((m) => m.text));
  const missing = [...primary, ...(secondary?.missing ?? [])
    .filter((m) => !said.has(m.text)).map((m) => ({ ...m, text: `${secondary.label}: ${m.text}` }))];
  root.hidden = !missing.length;
  if (!missing.length) return;
  const label = h('span', { class: 'missing-label' }, icon('info'), 'Henüz yeterli veri yok');
  put(root, missing.length === 1
    ? [h('p', {}, label), h('p', { class: 'missing-one', text: missing[0].text })]
    : [h('p', {}, label), h('ul', { class: 'missing-list' }, missing.map((m) => h('li', { text: m.text })))]);
}

export function renderRanking(root, data, { compact = false } = {}) {
  const min = data.thresholds.min_socket_samples;
  if (!data.ranking.length) {
    put(root, empty('Henüz hiçbir prizde ölçüm yok.'));
    return;
  }
  const detailOf = (r) => `${int(r.samples)} ölçüm, ${decimal(r.observed_days)} günlük kayıt`;
  const details = new Set(data.ranking.map(detailOf));
  const shared = details.size === 1 && data.ranking.every((r) => r.samples >= min) ? [...details][0] : null;
  const rows = data.ranking.map((r) => {
    const ranked = r.samples >= min;
    const detail = shared ? null : detailOf(r);
    return h('li', { class: `rank${ranked ? '' : ' is-unranked'}` },
      h('span', { class: 'rank-name', text: r.name }),
      ranked ? h('div', { class: 'bar', 'aria-hidden': 'true' }, h('span', { vars: { '--v': r.free_share } })) : null,
      h('span', { class: 'rank-value' }, ranked ? pct(r.free_share) : 'henüz sıralanmadı'),
      detail ? h('p', { class: 'quiet rank-detail', text: ranked ? detail : `${detail}. Sıralama için ${int(min)} ölçüm gerekiyor.` }) : null);
  });
  const list = h('ol', { class: `ranking${compact ? ' is-compact' : ''}`, 'aria-label': `${compact ? `${data.label}: p` : 'P'}rizler, en sık boş olandan başlayarak` }, rows);
  put(root, list, shared && !compact ? h('p', { class: 'quiet ranking-basis', text: `Her priz için ${shared}.` }) : null);
  return list;
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
    parts.push(h('p', { class: 'quiet', text: `${notes.join('; ')}.` }));
  }
  put(root, parts);
}

/** The secondary area's stay: its findings, short; a plain line while the engine still waits. */
export function stayBrief(data, findings) {
  const d = data.durations;
  if (findings.some((f) => f.key === 'occupancy') || d.median_min == null) return compactFindings(findings);
  const line = h('p', { class: 'brief', text: `Şimdiye kadar ${d.count} araç görüldü; genelde ${minutes(d.median_min)} kaldılar.` });
  return [line, compactFindings(findings)];
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

/** Site comparison: each area against the other Ecobox sites (primary bold, secondary quiet). */
export function renderSites(root, data, model) {
  const sites = data.sites;
  if (!sites) {
    put(root, empty('Diğer Ecobox siteleri henüz kaydedilmedi; karşılaştırma onlar kaydedilince gelecek.'));
    return;
  }
  const parts = [];
  const compare = [];
  const rowOf = (area) => area && sites.loads.find((l) => l.area === area.key);
  const primary = rowOf(model.primary);
  const secondary = rowOf(model.secondary);
  if (primary) compare.push(compareBar(primary.name, primary.busy_share, 'is-home'));
  if (secondary) compare.push(compareBar(secondary.name, secondary.busy_share, 'is-secondary'));
  if (sites.overall != null) compare.push(compareBar(`Diğer ${int(sites.others)} site`, sites.overall, ''));
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
  const rows = loads.map((l, i) => h('li', { class: `site${l.area === 'primary' ? ' is-home' : l.area ? ' is-secondary' : ''}` },
    h('span', { class: 'site-rank', text: String(i + 1) }),
    h('span', { class: 'site-name' }, l.name, h('span', { class: 'quiet', text: ` (${l.sockets} priz)` })),
    h('span', { class: 'site-value', text: pct(l.busy_share) }),
    h('div', { class: 'bar bar-busy', 'aria-hidden': 'true' }, h('span', { vars: { '--v': l.busy_share } }))));
  const n = loads.length;
  const list = h('div', { id: 'site-list', class: 'disclosure-body', hidden: true },
    h('p', { class: 'quiet sites-caption', text: `En az ${min} AC prizli siteler, en dolu olandan` }),
    h('ol', { class: 'sites', 'aria-label': 'Siteler, en dolu olandan başlayarak' }, rows));
  const text = h('span', { class: 'disclose-label' });
  const button = h('button', { type: 'button', class: 'disclose-row', 'aria-expanded': 'false', 'aria-controls': 'site-list' },
    text, h('span', { class: 'chevron' }, icon('chevron')));
  const label = (open) => { text.textContent = open ? 'Listeyi gizle' : `${min}+ prizli ${n} siteyi göster`; };
  button.addEventListener('click', () => {
    const open = list.hidden;
    list.hidden = !open;
    button.setAttribute('aria-expanded', String(open));
    label(open);
  });
  label(false);
  parts.push(h('div', { class: 'disclosure' }, button, list));
  put(root, parts);
}

function compareBar(name, share, kind) {
  return h('div', { class: `compare-row ${kind}`.trim() },
    h('span', { class: 'compare-name', text: name }),
    h('span', { class: 'compare-value', text: `${pct(share)} dolu` }),
    h('div', { class: 'bar bar-busy', 'aria-hidden': 'true' }, h('span', { vars: { '--v': share } })));
}

export function renderFooter(root, data) {
  const c = data.coverage;
  const lines = [];
  lines.push(c.first
    ? `Kayıt ${stamp(Date.parse(c.first))} ile ${stamp(Date.parse(c.last))} arası: ${decimal(c.observed_days)} günlük gözlem, ${c.dates} farklı gün, ${c.ac_sockets} AC priz. AC istatistikleri DC prizleri saymaz; DC'nin kendi bölümü var.`
    : 'Henüz kayıt yok.');
  const weeks = Math.round((Date.parse(data.window.until) - Date.parse(data.window.since)) / (7 * 86_400_000));
  lines.push(`İstatistikler ${stamp(Date.parse(data.generated_at))} itibarıyla, son ${weeks} haftanın kaydından hesaplandı. Saatler Türkiye saatidir.`);
  put(root, lines.map((text) => h('p', { text })));
}
