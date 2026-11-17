// The history sections below the live answer: findings, sockets, stay and arrivals, other
// sites, and the footer. Each takes its slice of data.json and fills its own container.

import { empty, h, put, svg } from './dom.js';
import { decimal, hourLabel, int, minutes, pct, stamp } from './format.js';

const SITES_SHOWN = 10;

export function renderFindings(root, data) {
  const cards = data.findings.map((f) => h('li', { class: 'finding' },
    h('span', { class: 'finding-emoji', 'aria-hidden': 'true', text: f.emoji }),
    h('div', {}, h('p', { class: 'finding-text', text: f.text }), h('p', { class: 'quiet', text: f.basis }))));
  const missing = data.missing.length
    ? h('div', { class: 'missing' },
      h('h3', { text: 'Henüz yeterli veri yok' }),
      h('ul', {}, data.missing.map((m) => h('li', { text: m.text }))))
    : null;
  put(root, cards.length ? h('ul', { class: 'findings' }, cards) : empty('Henüz söylenecek bir bulgu yok.'), missing);
}

export function renderRanking(root, data) {
  const min = data.thresholds.min_socket_samples;
  if (!data.ranking.length) {
    put(root, empty('Henüz hiçbir prizde okuma yok.'));
    return;
  }
  const detailOf = (r) => `${int(r.samples)} okuma · ${decimal(r.observed_days)} gün kayıt`;
  const details = new Set(data.ranking.map(detailOf));
  const shared = details.size === 1 && data.ranking.every((r) => r.samples >= min) ? [...details][0] : null;
  const rows = data.ranking.map((r) => {
    const ranked = r.samples >= min;
    const detail = shared ? null : detailOf(r);
    return h('li', { class: `rank${ranked ? '' : ' is-unranked'}` },
      h('div', { class: 'rank-top' },
        h('span', { class: 'rank-name', text: r.name }),
        h('span', { class: 'rank-value', text: ranked ? `${pct(r.free_share)} boş` : 'henüz sıralanmadı' })),
      h('div', { class: 'bar', 'aria-hidden': 'true' }, h('span', { vars: { '--v': ranked ? r.free_share : 0 } })),
      detail ? h('p', { class: 'quiet', text: ranked ? detail : `${detail} · sıralama için ${int(min)} okuma gerekiyor` }) : null);
  });
  put(root, h('ol', { class: 'ranking' }, rows), shared ? h('p', { class: 'quiet ranking-basis', text: `Her priz için ${shared}.` }) : null);
}

export function renderStay(root, data) {
  const d = data.durations;
  const minSessions = data.thresholds.min_sessions;
  const parts = [];
  if (d.median_min == null) {
    parts.push(empty('Prizde kalma süresi için henüz araç yok.'));
  } else {
    const sentence = d.count < minSessions
      ? `Şimdiye kadar ${d.count} araç görüldü; genelde ${minutes(d.median_min)} kaldılar. Sağlam bir sonuç için en az ${minSessions} araç gerekiyor.`
      : `Bir araç prizde genelde ${minutes(d.median_min)} kalıyor.`
        + (d.q1_min != null ? ` Araçların yarısı ${minutes(d.q1_min)} ile ${minutes(d.q3_min)} arasında ayrılıyor.` : '');
    parts.push(h('p', { class: 'lead', text: sentence }), quartileBar(d));
    const basis = `${int(d.count)} araca göre` + (d.excluded ? `; kayıt kesintisine denk gelen ${int(d.excluded)} araç sayılmadı.` : '.');
    parts.push(h('p', { class: 'quiet', text: basis }));
  }
  parts.push(h('h3', { text: 'Saate göre takma ve çıkarma' }), arrivalsChart(data.plug_in, data.unplug));
  const excluded = data.plug_in.excluded + data.unplug.excluded;
  const notes = [`${int(data.plug_in.count)} takma, ${int(data.unplug.count)} çıkarma`];
  if (excluded) notes.push(`saati tam bilinmeyen ${int(excluded)} takma ya da çıkarma sayılmadı`);
  if (Math.max(data.plug_in.count, data.unplug.count) < data.thresholds.min_events) notes.push('henüz az');
  parts.push(h('p', { class: 'quiet', text: `${notes.join(' · ')}.` }));
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
    h('figcaption', { class: 'quiet' },
      d.q1_min != null ? h('span', { class: 'key key-box' }) : null, d.q1_min != null ? 'araçların yarısı  ' : null,
      h('span', { class: 'key key-median' }), 'genelde'));
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
    h('figcaption', { class: 'quiet' },
      h('span', { class: 'key key-plug' }), 'takma  ', h('span', { class: 'key key-unplug' }), 'çıkarma'));
}

export function renderSites(root, data) {
  const sites = data.sites;
  if (!sites) {
    put(root, empty('Diğer Ecobox siteleri henüz kaydedilmedi; karşılaştırma onlar kaydedilince gelecek.'));
    return;
  }
  const parts = [];
  const compare = [];
  if (sites.home) compare.push(compareBar(`${sites.home.name}`, sites.home.busy_share, true));
  if (sites.overall != null) compare.push(compareBar(`Diğer ${int(sites.others)} site`, sites.overall, false));
  parts.push(h('div', { class: 'compare' }, compare));
  if (sites.overall == null) {
    parts.push(h('p', { class: 'quiet', text: 'Diğer sitelerle karşılaştırma için henüz yeterli ortak kayıt yok.' }));
  } else {
    parts.push(h('p', { class: 'quiet', text: `Prizlerin dolu olduğu zaman payı · ${decimal(sites.common_days)} günlük ortak kayıt.` }));
  }
  const min = data.thresholds.min_site_sockets;
  const loads = sites.loads.filter((l) => l.home || l.sockets >= min);
  const rows = loads.map((l, i) => h('li', { class: `site${l.home ? ' is-home' : ''}` },
    h('span', { class: 'site-rank', text: String(i + 1) }),
    h('span', { class: 'site-name' }, l.name, h('span', { class: 'quiet', text: ` · ${l.sockets} priz` })),
    h('span', { class: 'site-value', text: pct(l.busy_share) }),
    h('div', { class: 'bar bar-busy', 'aria-hidden': 'true' }, h('span', { vars: { '--v': l.busy_share } }))));
  const homeIndex = loads.findIndex((l) => l.home);
  const list = h('ol', { class: 'sites', 'aria-label': 'Siteler, en dolu olandan başlayarak' }, rows);
  const collapse = (collapsed) => rows.forEach((row, i) => {
    row.hidden = collapsed && i >= SITES_SHOWN && i !== homeIndex;
  });
  parts.push(h('h3', { text: `En az ${min} AC prizli siteler, en dolu olandan` }), list);
  if (rows.length > SITES_SHOWN) {
    let collapsed = true;
    const button = h('button', { type: 'button', class: 'more', 'aria-expanded': 'false' });
    const label = () => { button.textContent = collapsed ? `Tümünü göster (${rows.length})` : 'Daha az göster'; };
    button.addEventListener('click', () => {
      collapsed = !collapsed;
      collapse(collapsed);
      button.setAttribute('aria-expanded', String(!collapsed));
      label();
    });
    collapse(true);
    label();
    parts.push(button);
  }
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
