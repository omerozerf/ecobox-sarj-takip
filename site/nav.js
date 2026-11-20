// The sticky section tabs: plain links, with an underline under the section in view. An
// IntersectionObserver watches a band just under the bar; the first section crossing it is the
// current one. The last section is short and may never reach the band, so the page's bottom
// edge also selects it. A tab the layout hides (on a wide screen "Şu an" is a sticky column,
// always in view) is never selected.

const reducedMotion = () => matchMedia('(prefers-reduced-motion: reduce)').matches;

export function followSections(nav) {
  const links = [...nav.querySelectorAll('a[href^="#"]')];
  const row = nav.querySelector('.tabs-row');
  const targets = links.map((a) => document.getElementById(a.hash.slice(1)));
  const inBand = new Set();
  let current = null;

  function select(index) {
    if (index === current) return;
    current = index;
    links.forEach((a, i) => {
      a.classList.toggle('is-active', i === index);
      if (i === index) a.setAttribute('aria-current', 'location');
      else a.removeAttribute('aria-current');
    });
    // Keep the active tab visible inside the row without scrolling the page.
    const link = links[index];
    const left = link.offsetLeft - row.offsetLeft;
    if (left < row.scrollLeft || left + link.offsetWidth > row.scrollLeft + row.clientWidth) {
      row.scrollTo({ left: Math.max(0, left - 16), behavior: reducedMotion() ? 'auto' : 'smooth' });
    }
  }

  const atBottom = () => window.innerHeight + window.scrollY >= document.documentElement.scrollHeight - 4;
  const shown = (i) => links[i].offsetParent !== null;
  function update() {
    if (atBottom()) {
      select(targets.length - 1);
      return;
    }
    const first = targets.findIndex((t, i) => shown(i) && inBand.has(t));
    if (first >= 0) select(first);
    else if (current == null || !shown(current)) select(links.findIndex((_, i) => shown(i)));
  }

  const top = nav.offsetHeight;
  const observer = new IntersectionObserver((entries) => {
    for (const entry of entries) {
      if (entry.isIntersecting) inBand.add(entry.target);
      else inBand.delete(entry.target);
    }
    update();
  }, { rootMargin: `-${top + 16}px 0px -60% 0px` });
  targets.forEach((t) => observer.observe(t));
  window.addEventListener('scroll', update, { passive: true }); // cheap: a set lookup per tab
  window.addEventListener('resize', update, { passive: true });
  update();
}
