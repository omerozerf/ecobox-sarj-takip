// The page's own icon set, drawn for it: a 24 × 24 grid, 1.75 px round strokes in currentColor,
// fills only where a shape needs one (plug pins). Status icons are the AC plug with a small badge
// in the lower right, so a tile reads "socket + what it is doing" at 16 px. One illustration (an
// AC charge post with its cable) sits in the header and in the live card's empty states.

import { svg } from './dom.js';

// A smaller plug, top left, leaving the lower right corner for a status badge.
const SMALL_PLUG = [
  ['path', { d: 'M5 4.5h8a1.8 1.8 0 0 1 1.7 2.4l-.9 2.9A5 5 0 0 1 9 14a5 5 0 0 1-4.8-4.2l-.9-2.9A1.8 1.8 0 0 1 5 4.5z' }],
  ['circle', { cx: 7, cy: 8, r: 0.9, class: 'fill' }],
  ['circle', { cx: 11, cy: 8, r: 0.9, class: 'fill' }],
  ['path', { d: 'M9 14v2.5A2.5 2.5 0 0 1 6.5 19H4' }],
];

const SHAPES = {
  // Type 2 face (flat top, round bottom) with three pins and the cable.
  ac: [
    ['path', { d: 'M6.6 3h10.8a2 2 0 0 1 1.9 2.6l-.9 3A7 7 0 0 1 12 14a7 7 0 0 1-6.4-5.4l-.9-3A2 2 0 0 1 6.6 3z' }],
    ['circle', { cx: 9.4, cy: 6.6, r: 1, class: 'fill' }],
    ['circle', { cx: 14.6, cy: 6.6, r: 1, class: 'fill' }],
    ['circle', { cx: 12, cy: 10.2, r: 1, class: 'fill' }],
    ['path', { d: 'M12 14v3.5a3 3 0 0 0 3 3h4' }],
  ],
  // CCS: the AC face with a bolt in it, and the two DC pins below.
  dc: [
    ['path', { d: 'M6.6 2.5h10.8a2 2 0 0 1 1.9 2.6l-.7 2.4A6.2 6.2 0 0 1 12 12a6.2 6.2 0 0 1-6.6-4.5l-.7-2.4a2 2 0 0 1 1.9-2.6z' }],
    ['path', { d: 'M12.9 4.6 10.8 7.6h2.6l-2 3' }],
    ['rect', { x: 5.5, y: 14.5, width: 13, height: 7, rx: 3.5 }],
    ['circle', { cx: 9.3, cy: 18, r: 1.2, class: 'fill' }],
    ['circle', { cx: 14.7, cy: 18, r: 1.2, class: 'fill' }],
  ],
  free: [...SMALL_PLUG, ['path', { d: 'M14.8 17.4l2 2 4-4.4' }]],
  busy: [...SMALL_PLUG, ['path', { d: 'M19.2 12.8 16.4 17h3.2l-2.4 4.2' }]],
  fault: [...SMALL_PLUG, ['path', { d: 'M18 13v4.3M18 20.6v.1' }]],
  unknown: [...SMALL_PLUG, ['path', { d: 'M16 14.9a2 2 0 1 1 2.8 1.8c-.5.2-.8.6-.8 1.1v.4M18 20.7v.1' }]],
  clock: [
    ['circle', { cx: 12, cy: 12, r: 8.75 }],
    ['path', { d: 'M12 7.5V12l3 2' }],
  ],
  // A car driving off to the right, motion lines behind it.
  leaving: [
    ['path', { d: 'M6.5 16.5H6v-2.8c0-.6.4-1 .9-1.2l2.1-.6 2-2.7c.3-.5.9-.7 1.4-.7h4c.6 0 1.1.3 1.4.7l2.2 2.8 1.1.3c.6.2 1 .7 1 1.3v2.9h-.6M11 16.5h5.6' }],
    ['circle', { cx: 8.8, cy: 16.6, r: 1.8 }],
    ['circle', { cx: 18.4, cy: 16.6, r: 1.8 }],
    ['path', { d: 'M1.5 10.5h3M1 13.5h3M2 16.5h1.8' }],
  ],
  calendar: [
    ['rect', { x: 3.5, y: 5, width: 17, height: 15.5, rx: 2.5 }],
    ['path', { d: 'M3.5 9.5h17M8 3v4M16 3v4M8 13.5h2M14 13.5h2M8 16.8h2' }],
  ],
  pin: [
    ['path', { d: 'M12 21s-6.5-5.5-6.5-11a6.5 6.5 0 0 1 13 0c0 5.5-6.5 11-6.5 11z' }],
    ['circle', { cx: 12, cy: 10, r: 2.4 }],
  ],
  info: [
    ['circle', { cx: 12, cy: 12, r: 8.75 }],
    ['path', { d: 'M12 11v5.5M12 7.8v.1' }],
  ],
  warn: [['path', { d: 'M10.4 4.6a1.8 1.8 0 0 1 3.2 0l7.1 12.8a1.8 1.8 0 0 1-1.6 2.6H4.9a1.8 1.8 0 0 1-1.6-2.6zM12 9.8v4M12 16.8v.1' }]],
  chevron: [['path', { d: 'M7 10l5 5 5-5' }]],
};

export const ICON_NAMES = Object.keys(SHAPES);

/** A stroke icon by name; decorative (the text next to it carries the meaning). */
export function icon(name) {
  return svg('svg', { class: `icon icon-${name}`, viewBox: '0 0 24 24', 'aria-hidden': 'true', focusable: 'false' },
    SHAPES[name].map(([tag, props]) => svg(tag, props)));
}

/**
 * The AC charge post with its cable: the post and plug in the accent, the cable, screen and
 * ground in the muted tone. Decorative.
 */
export function chargePost() {
  return svg('svg', { class: 'art', viewBox: '0 0 64 64', 'aria-hidden': 'true', focusable: 'false' },
    svg('path', { class: 'art-muted', d: 'M8 58h48' }),
    svg('rect', { class: 'art-accent', x: 14, y: 7, width: 22, height: 51, rx: 6 }),
    svg('rect', { class: 'art-muted art-soft', x: 19, y: 13, width: 12, height: 9, rx: 2 }),
    svg('path', { class: 'art-accent', d: 'M26 15.2 23.6 18.4h2.8l-2.2 3' }),
    svg('circle', { class: 'art-accent', cx: 25, cy: 33, r: 4.5 }),
    svg('path', { class: 'art-muted', d: 'M36 40c7 0 11 2.5 11 9v1' }),
    svg('rect', { class: 'art-accent', x: 42, y: 50, width: 10, height: 6, rx: 2 }),
    svg('path', { class: 'art-accent', d: 'M45 56v2M49 56v2' }));
}

/** Fills the static page's placeholders: `<span data-icon="clock">` and `<span data-art>`. */
export function hydrateIcons(root = document) {
  root.querySelectorAll('[data-icon]').forEach((el) => el.replaceChildren(icon(el.dataset.icon)));
  root.querySelectorAll('[data-art]').forEach((el) => el.replaceChildren(chargePost()));
}
