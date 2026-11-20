// Entry point: load data.json, fill every section, then keep the live answer fresh.

import { empty, put } from './dom.js';
import { renderWeekly } from './heatmap.js';
import { hydrateIcons } from './icons.js';
import { LiveFeed } from './live.js';
import { LiveView } from './live-view.js';
import { renderPopular } from './popular.js';
import {
  findingsBySection, renderFindingSlots, renderFooter, renderOtherFindings, renderRanking, renderSites, renderStay,
} from './sections.js';

const REFRESH_MS = 3 * 60_000;
const RETURN_REFRESH_MS = 60_000;
const TICK_MS = 30_000;

const $ = (selector) => document.querySelector(selector);

async function loadData() {
  const response = await fetch('./data.json', { cache: 'no-cache' });
  if (!response.ok) throw new Error(`data.json: HTTP ${response.status}`);
  return response.json();
}

/** Fills the history sections; returns the parts that follow the live reading. */
function renderHistory(data) {
  const findings = findingsBySection(data.findings);
  const parts = { popular: null, weekly: null };
  const sections = [
    ['#ne-zaman', '[data-pt=chart]', (el) => {
      parts.popular = renderPopular(el, data, { onHours: (all) => parts.weekly?.setAll(all) });
    }],
    ['#ne-zaman', '[data-hm=panel]', (el) => {
      parts.weekly = renderWeekly(el, data);
      renderFindingSlots(el, findings.when);
    }],
    ['#prizler', null, (el) => { renderRanking(el.querySelector('[data-section=ranking]'), data); renderFindingSlots(el, findings.sockets); }],
    ['#sure', null, (el) => { renderStay(el.querySelector('[data-section=stay]'), data, findings.stay); renderFindingSlots(el, findings.stay); }],
    ['#siteler', null, (el) => { renderSites(el.querySelector('[data-section=sites]'), data); renderFindingSlots(el, findings.sites); }],
    ['#notlar', null, (el) => renderOtherFindings(el, findings.other, data.missing)],
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
    if (root.classList.contains('card')) root.classList.add('rise');
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
  const { popular } = renderHistory(data);
  if (popular) {
    const follow = () => popular.live({ status: view.status(), failed: view.failed, settled: view.settled });
    view.onChange = follow;
    follow();
  }
}

main();
