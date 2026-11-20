// Keeps a sticky column usable when it is taller than the window. CSS stickiness pins the top
// edge, which would hide the column's lower rows for good; instead the column pins at its
// natural offset while it fits and by its bottom edge (a negative top) when it does not.

export function stickToViewport(el, gap = 16) {
  const place = () => {
    const natural = (parseFloat(getComputedStyle(document.documentElement).getPropertyValue('--tabs-h')) || 56) + gap;
    const fit = window.innerHeight - el.offsetHeight - gap;
    el.style.setProperty('--live-top', `${Math.min(natural, fit)}px`);
  };
  new ResizeObserver(place).observe(el);
  window.addEventListener('resize', place);
  place();
}
