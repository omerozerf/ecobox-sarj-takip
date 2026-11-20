// Entry point: load data.json, fill every section, then keep the live answer fresh.

import { areaModel } from './areas.js';
import { renderDc } from './dc.js';
import { empty, h, put } from './dom.js';
import { renderWeekly } from './heatmap.js';
import { hydrateIcons } from './icons.js';
import { LiveFeed } from './live.js';
import { LiveView } from './live-view.js';
import { renderPopular } from './popular.js';
import {
  compactFindings, findingsBySection, renderFindingSlots, renderFooter, renderMissing, renderOtherFindings, renderRanking,
  renderSecondary, renderSites, renderStay, stayBrief,
} from './sections.js';
import { followSections } from './nav.js';
import { stickToViewport } from './sticky.js';

const REFRESH_MS = 3 * 60_000;
const RETURN_REFRESH_MS = 60_000;
const TICK_MS = 30_000;

const $ = (selector) => document.querySelector(selector);

async function loadData() {
  const response = await fetch('./data.json', { cache: 'no-cache' });
  if (!response.ok) throw new Error(`data.json: HTTP ${response.status}`);
  return response.json();
}

/**
 * The two-option switch over the "Ne zaman" chart: primary (default) or secondary area.
 * Calls onPick(key) when the other one is picked.
 */
function areaSwitch(root, model, onPick) {
  if (!model.secondary) return;
  const options = [model.primary, model.secondary].map((area) => h('button', {
    type: 'button', class: 'area-option', 'aria-pressed': String(area === model.primary), 'data-key': area.key, text: area.label,
  }));
  options.forEach((button) => button.addEventListener('click', () => {
    if (button.getAttribute('aria-pressed') === 'true') return;
    options.forEach((b) => b.setAttribute('aria-pressed', String(b === button)));
    onPick(button.dataset.key);
  }));
  put(root, options);
  root.hidden = false;
}

/** Fills the history sections; returns the parts that follow the live reading. */
function renderHistory(data) {
  const model = areaModel(data);
  const primary = model.view(model.primary);
  const secondary = model.secondary ? model.view(model.secondary) : null;
  const findings = findingsBySection(primary.findings);
  const other = findingsBySection(secondary?.findings ?? []);
  const parts = { popular: null, weekly: null, area: 'primary' };
  const sections = [
    ['#ne-zaman', '[data-pt=chart]', (el) => {
      parts.popular = renderPopular(el, primary, { onHours: (all) => parts.weekly?.setAll(all) });
    }],
    ['#ne-zaman', '[data-hm=panel]', (el) => {
      parts.weekly = renderWeekly(el, primary);
      // The findings under the chart follow the switch: the secondary ones short, as elsewhere.
      const whenFindings = (key) => (key === 'secondary'
        ? put(el.querySelector('[data-findings=rest]'), compactFindings(other.when))
        : renderFindingSlots(el, findings.when));
      whenFindings('primary');
      areaSwitch(el.querySelector('[data-area-switch]'), model, (key) => {
        const view = key === 'secondary' ? secondary : primary;
        parts.area = key;
        el.dataset.area = key;
        parts.popular?.setArea(view);
        parts.weekly?.setArea(view);
        whenFindings(key);
        parts.onArea?.();
      });
    }],
    ['#dc', '[data-dc=chart]', (el) => {
      if (renderDc(el, data)) $('[data-tab=dc]').hidden = false;
    }],
    ['#prizler', null, (el) => {
      renderRanking(el.querySelector('[data-section=ranking]'), primary);
      renderFindingSlots(el, findings.sockets);
      if (secondary) {
        const holder = h('div');
        if (secondary.ranking.length) renderRanking(holder, secondary, { compact: true });
        renderSecondary(el.querySelector('[data-section=ranking-secondary]'), secondary, holder.firstChild, compactFindings(other.sockets));
      }
    }],
    ['#sure', null, (el) => {
      renderStay(el.querySelector('[data-section=stay]'), primary, findings.stay);
      renderFindingSlots(el, findings.stay);
      if (secondary) renderSecondary(el.querySelector('[data-section=stay-secondary]'), secondary, stayBrief(secondary, other.stay));
    }],
    ['#siteler', null, (el) => {
      renderSites(el.querySelector('[data-section=sites]'), data, model);
      renderFindingSlots(el, findings.sites);
      // The secondary area's comparison is its quiet bar above; its finding would repeat it.
    }],
    ['#notlar', null, (el) => renderOtherFindings(el, findings.other, secondary, other.other)],
    ['[data-section=missing]', null, (el) => renderMissing(el, primary.missing, secondary)],
    ['[data-section=footer]', null, (el) => renderFooter(el, data)],
  ];
  for (const [selector, part, render] of sections) {
    const root = $(selector);
    try {
      render(root);
    } catch (error) { // one broken part must not take the page down
      console.error(selector, part, error);
      const target = part ? root.querySelector(part) : root;
      put(target.querySelector('[aria-busy]') ?? target, empty('Bu bölüm gösterilemedi.'));
    }
    if (root.classList.contains('block')) root.classList.add('rise');
  }
  return parts;
}

function startLive(data) {
  const view = new LiveView($('#simdi'), data);
  const feed = new LiveFeed(data.snapshot_base_url);
  const gapMs = data.thresholds.gap_s * 1000;
  let lastAttempt = 0;
  let running = false;

  async function refresh() {
    if (running) return;
    running = true;
    lastAttempt = Date.now();
    if (!view.result) view.loading();
    try {
      view.show(await feed.read(data.sockets, data.states, gapMs));
    } catch (error) {
      console.warn('live status:', error);
      view.fail();
    } finally {
      running = false;
    }
  }

  view.onRetry = refresh;
  refresh();
  setInterval(() => {
    if (document.visibilityState === 'visible' && Date.now() - lastAttempt >= REFRESH_MS) refresh();
  }, 15_000);
  setInterval(() => view.render(), TICK_MS);
  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState !== 'visible') return;
    view.render();
    if (Date.now() - lastAttempt >= RETURN_REFRESH_MS) refresh();
  });
  return view;
}

async function main() {
  hydrateIcons();
  followSections($('.tabs'));
  stickToViewport($('.block.live'));
  let data;
  try {
    data = await loadData();
  } catch (error) {
    console.error(error);
    $('[data-live=big]').textContent = 'Sayfa verisi yüklenemedi';
    $('[data-live=sub]').textContent = 'Biraz sonra sayfayı yenilemeyi dene.';
    document.querySelectorAll('[aria-busy=true]').forEach((el) => put(el, empty('Veri yüklenemedi.')));
    return;
  }
  const view = startLive(data);
  const parts = renderHistory(data);
  if (parts.popular) {
    const follow = () => parts.popular.live({ status: view.status(parts.area), failed: view.failed, settled: view.settled });
    view.onChange = follow;
    parts.onArea = follow;
    follow();
  }
}

main();
