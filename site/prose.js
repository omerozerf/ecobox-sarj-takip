// Reading aids for the engine's sentences, without the DOM so they can be tested under Node:
// split a finding into sentences (one idea per line on the page) and mark the figures a reader
// looks for first (time ranges, percentages, durations) so they can be set in bold.

// A sentence ends at . ! ? followed by a capital, but not after an ordinal ("3. Arge").
const SENTENCE_END = /(?<=[.!?])\s+(?=\p{Lu})/gu;
const ORDINAL_END = /(?:^|\s)\d+\.$/;

/** The sentences of a text, in order; a text without an end mark is one sentence. */
export function sentences(text) {
  const out = [];
  let start = 0;
  for (const match of text.matchAll(SENTENCE_END)) {
    const before = text.slice(start, match.index);
    if (ORDINAL_END.test(before)) continue;
    out.push(before);
    start = match.index + match[0].length;
  }
  out.push(text.slice(start));
  return out.filter((s) => s.trim());
}

/** [first sentence, the rest ('' when there is none)]. */
export function leadSentence(text) {
  const [first = '', ...rest] = sentences(text);
  return [first, rest.join(' ')];
}

// 09:00–10:00 · 07:30 · %42 and %42'si · 3 sa 57 dk, 2 sa, 45 dk.
// A unit must end its word: "4 saatten" is not a duration.
const FIGURE = /\d{2}:\d{2}(?:–\d{2}:\d{2})?|%\d+(?:'[\p{Ll}]+)?|\d+ sa(?: \d+ dk)?(?!\p{L})|\d+ dk(?!\p{L})/gu;

/** [{text, strong}] covering the whole text; figures are the strong parts. */
export function emphasis(text) {
  const parts = [];
  let at = 0;
  for (const match of text.matchAll(FIGURE)) {
    if (match.index > at) parts.push({ text: text.slice(at, match.index), strong: false });
    parts.push({ text: match[0], strong: true });
    at = match.index + match[0].length;
  }
  if (at < text.length) parts.push({ text: text.slice(at), strong: false });
  return parts;
}
