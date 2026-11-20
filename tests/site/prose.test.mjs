// Run: node --test tests/site/  (also run by tests/test_site_prose.py when Node is installed).
import assert from 'node:assert/strict';
import { test } from 'node:test';

import { emphasis, leadSentence, sentences } from '../../site/prose.js';

const strong = (text) => emphasis(text).filter((p) => p.strong).map((p) => p.text);

test('a finding splits into its sentences', () => {
  assert.deepEqual(
    sentences('Hafta içi boş priz en kolay 00:00–01:00 arası bulunur (şans %100). En zor saat 09:00–10:00 (şans %23); o saatte gidersen büyük ihtimalle beklersin.'),
    ['Hafta içi boş priz en kolay 00:00–01:00 arası bulunur (şans %100).',
      'En zor saat 09:00–10:00 (şans %23); o saatte gidersen büyük ihtimalle beklersin.']);
});

test('an ordinal or a decimal is not a sentence end', () => {
  assert.deepEqual(
    sentences('En sık boş olan priz 3. Arge Otopark 1: zamanın %76\'sında boş. Ardından 3. Arge Otopark 2 (%74) geliyor.'),
    ['En sık boş olan priz 3. Arge Otopark 1: zamanın %76\'sında boş.',
      'Ardından 3. Arge Otopark 2 (%74) geliyor.']);
  assert.deepEqual(sentences('En az kullanılan F: günde 1,1 kez. Hepsi birlikte günde yaklaşık 12,1 kez takılıyor.'),
    ['En az kullanılan F: günde 1,1 kez.', 'Hepsi birlikte günde yaklaşık 12,1 kez takılıyor.']);
  assert.deepEqual(sentences('Ortalama %33. Yani ortalamaya yakın.'), ['Ortalama %33.', 'Yani ortalamaya yakın.']);
});

test('a text without an end mark is one sentence; the lead is the first', () => {
  assert.deepEqual(sentences('Arıza yok'), ['Arıza yok']);
  assert.deepEqual(leadSentence('Bir. İki. Üç.'), ['Bir.', 'İki. Üç.']);
  assert.deepEqual(leadSentence('Tek cümle.'), ['Tek cümle.', '']);
});

test('times, percentages and durations are the strong parts', () => {
  assert.deepEqual(strong('Bir araç prizde genelde 3 sa 57 dk kalıyor. Araçların çoğu 2 sa 12 dk ile 5 sa arası kalıyor.'),
    ['3 sa 57 dk', '2 sa 12 dk', '5 sa']);
  assert.deepEqual(strong('Prize takma en çok 07:00–09:00 arası (takmaların %42\'si).'), ['07:00–09:00', '%42\'si']);
  assert.deepEqual(strong('DC 48 dk, son ölçüm 14:05'), ['48 dk', '14:05']);
  assert.deepEqual(strong('günde 2,1 kez, 8 priz'), []);
  assert.deepEqual(strong('Araçların %49\'u 4 saatten uzun kalıyor.'), ['%49\'u']);
  assert.deepEqual(strong('5 dkdan'), []);
});

test('the parts put back together are the text', () => {
  const text = 'Ego, F Blok ve DC\'de AC prizler zamanın %28\'inde dolu. Diğer sitelerde %33.';
  assert.equal(emphasis(text).map((p) => p.text).join(''), text);
  assert.deepEqual(emphasis(''), []);
});
