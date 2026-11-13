const { test } = require('node:test');
const assert = require('node:assert/strict');
const { InventoryQueryCache } = require('../inventory_query_cache');

test('identical concurrent visitors share one source call; expiry and manual refresh reload', async () => {
  let now = 1000;
  let calls = 0;
  const cache = new InventoryQueryCache({ now: () => now, ttl: 100 });
  const parameters = { stock_date: '2026-11-05', sku_codes: ['S'] };
  const load = async () => ({ ok: true, rows: [{ quantity: ++calls }], queried_at: 'original' });
  const both = await Promise.all([cache.get('stock', parameters, load), cache.get('stock', parameters, load)]);
  assert.equal(calls, 1);
  assert.deepEqual(both[0], both[1]);
  const hit = await cache.get('stock', parameters, load);
  assert.equal(hit.cache.hit, true);
  assert.equal(hit.queried_at, 'original');
  await cache.get('stock', parameters, load, true);
  assert.equal(calls, 2);
  now += 101;
  await cache.get('stock', parameters, load);
  assert.equal(calls, 3);
});

test('dates and windows are isolated; errors are never cached', async () => {
  const cache = new InventoryQueryCache();
  let calls = 0;
  const load = async () => ({ ok: false, code: 'DB_QUERY_FAILED', calls: ++calls });
  await cache.get('stock', { stock_date: '2026-11-04' }, load);
  await cache.get('stock', { stock_date: '2026-11-04' }, load);
  assert.equal(calls, 2);
  await cache.get('movements', { days: 7 }, async () => ({ ok: true, rows: [7] }));
  assert.equal(cache.peek('movements', { days: 120 }), null);
  assert.equal(cache.peek('stock', { stock_date: '2026-11-05' }), null);
});

test('refresh invalidates same-day stock variants and pending movement results', async () => {
  const cache = new InventoryQueryCache();
  await cache.get('stock', { sku: 'S' }, async () => ({ ok: true, rows: [], stock_date: '2026-11-05' }));
  await cache.get('stock', { sku: 'T' }, async () => ({ ok: true, rows: [], stock_date: '2026-11-04' }));
  let finish;
  const pending = cache.get('movements', {}, () => new Promise(resolve => { finish = resolve; }));
  await Promise.resolve();
  cache.invalidateStockDate('2026-11-05');
  finish({ ok: true, rows: [1] });
  await pending;
  assert.equal(cache.peek('movements', {}), null);
  assert.equal(cache.peek('stock', { sku: 'S' }), null);
  assert.ok(cache.peek('stock', { sku: 'T' }));
});

test('memory is bounded and a rejected load can be retried', async () => {
  const cache = new InventoryQueryCache({ maxBytes: 70 });
  await cache.get('stock', { n: 1 }, async () => ({ ok: true, rows: ['a'.repeat(20)] }));
  await cache.get('stock', { n: 2 }, async () => ({ ok: true, rows: ['b'.repeat(20)] }));
  assert.equal(cache.entries.size, 1);
  assert.ok(cache.bytes <= 70);
  await assert.rejects(cache.get('stock', {}, async () => { throw new Error('failed'); }));
  const retry = await cache.get('stock', {}, async () => ({ ok: true, rows: [] }));
  assert.equal(retry.ok, true);
});
