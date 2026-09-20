const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');

function deferred() {
  let resolve;
  const promise = new Promise(done => { resolve = done; });
  return { promise, resolve };
}

function setup(jsonApi, api) {
  const elements = new Map();
  const $ = selector => {
    if (!elements.has(selector)) elements.set(selector, {
      innerHTML: '', textContent: '', open: false,
      addEventListener() {}, showModal() { this.open = true; },
    });
    return elements.get(selector);
  };
  const context = vm.createContext({ $, jsonApi, api, detailVersion: 0, previewUrl: null,
    escape: String, badge: () => '', fiscalDetail: () => '', accountingDetail: () => '',
    toast: message => { throw new Error(message); },
    URL: { createObjectURL: blob => `blob:${blob}`, revokeObjectURL() {} },
  });
  const source = fs.readFileSync(path.join(__dirname, '../static/app.js'), 'utf8');
  const start = source.indexOf('function readingNotice(doc)');
  const end = source.indexOf("$('#detail-dialog').addEventListener('close'", start);
  vm.runInContext(source.slice(start, end), context);
  return { context, $ };
}

const doc = id => ({ id, filename: `${id}.png`, kind: 'receipts', status: 'success', result: {} });
const tick = async () => { await Promise.resolve(); await Promise.resolve(); };

test('non-fiscal receipt asks for original document rather than clearer photo', () => {
  const { context } = setup();
  const html = context.readingNotice({ status:'review', result:{raw_text:'test',seller_name:'TEST',issues:['Bilgi fişi / mali değeri olmayan belge: muhasebe aktarımı için asıl faturayı yükleyin.']} });
  assert.match(html, /Belge okundu; aktarım için ek belge gerekli/);
  assert.match(html, /asıl faturayı veya Z raporunu/);
  assert.doesNotMatch(html, /daha net bir fotoğrafı/);
});

test('unspecified card type explains the missing POS evidence', () => {
  const { context } = setup();
  const html = context.readingNotice({ status:'review', result:{raw_text:'test',seller_name:'TEST',issues:['Kartın banka kartı mı kredi kartı mı olduğu okunamadı.']} });
  assert.match(html, /POS slipi/);
  assert.match(html, /Belge okundu/);
});

test('late document response cannot replace the newer selected document', async () => {
  const first = deferred();
  const { context, $ } = setup(url => url.endsWith('/A') ? first.promise : Promise.resolve(doc('B')),
    async url => ({ blob: async () => url.includes('/B/') ? 'B' : 'A' }));
  const pending = context.showDetail('A');
  await context.showDetail('B');
  first.resolve(doc('A'));
  await pending;
  assert.equal($('#detail-title').textContent, 'B.png');
  assert.match($('#source-preview').innerHTML, /blob:B/);
});

test('late source image cannot be shown next to another documents data', async () => {
  const firstImage = deferred();
  const { context, $ } = setup(async url => doc(url.split('/').pop()),
    async url => ({ blob: () => url.includes('/A/') ? firstImage.promise : Promise.resolve('B') }));
  const pending = context.showDetail('A');
  await tick();
  await context.showDetail('B');
  firstImage.resolve('A');
  await pending;
  assert.equal($('#detail-title').textContent, 'B.png');
  assert.match($('#source-preview').innerHTML, /blob:B/);
  assert.equal($('#source-download').download, 'B.png');
});
