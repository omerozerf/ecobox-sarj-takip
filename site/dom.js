// Element builders. Strings always become text nodes, so data never reaches innerHTML.

import { emphasis, sentences } from './prose.js';

export function h(tag, props = {}, ...children) {
  return fill(document.createElement(tag), props, children);
}

export function svg(tag, props = {}, ...children) {
  return fill(document.createElementNS('http://www.w3.org/2000/svg', tag), props, children);
}

function fill(node, props, children) {
  for (const [key, value] of Object.entries(props)) {
    if (value == null || value === false) continue;
    if (key === 'class') node.setAttribute('class', value);
    else if (key === 'text') node.textContent = value;
    else if (key === 'vars') for (const [name, v] of Object.entries(value)) node.style.setProperty(name, v);
    else if (key.startsWith('on')) node.addEventListener(key.slice(2), value);
    else node.setAttribute(key, value === true ? '' : value);
  }
  node.append(...children.flat().filter((c) => c != null && c !== false));
  return node;
}

/** Replaces a container's content. */
export function put(container, ...children) {
  container.replaceChildren(...children.flat().filter((c) => c != null && c !== false));
  container.removeAttribute('aria-busy');
}

/**
 * An engine sentence set for reading: one line per sentence, the figures (times, percentages,
 * durations) in bold. Text nodes only.
 */
export function prose(text) {
  return sentences(text).map((sentence) => h('span', { class: 'sentence' },
    emphasis(sentence).map((part) => (part.strong ? h('strong', { text: part.text }) : part.text))));
}

/** A quiet "nothing to show yet" line. */
export const empty = (text) => h('p', { class: 'empty', text });

/**
 * A chart's readout: the value in bold, the detail muted on its own line under it. Unchanged
 * text is left alone, so an aria-live readout does not announce it again.
 */
export function readout(el, head, detail = '') {
  const key = `${head}\u0000${detail}`;
  if (el.dataset.key === key) return;
  el.dataset.key = key;
  el.replaceChildren(...[
    head ? h('strong', { text: head }) : null,
    head && detail ? ' ' : null,
    detail ? h('span', { class: 'readout-detail', text: detail }) : null,
  ].filter(Boolean));
}

/** Arrow-key navigation over a set of buttons with one tab stop (roving tabindex). */
export function roving(buttons, start, next, onFocus) {
  let current = start;
  buttons.forEach((b, i) => {
    b.tabIndex = i === current ? 0 : -1;
    b.addEventListener('click', () => select(i, false));
    b.addEventListener('keydown', (event) => {
      const target = next(i, event.key);
      if (target == null || target === i) return;
      event.preventDefault();
      select(target, true);
    });
  });
  function select(i, focus) {
    buttons[current].tabIndex = -1;
    current = i;
    buttons[i].tabIndex = 0;
    if (focus) buttons[i].focus();
    onFocus(i);
  }
}
