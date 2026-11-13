const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs/promises');
const os = require('node:os');
const path = require('node:path');
const { DashboardSnapshotStore } = require('../dashboard_snapshot_store');
const { latestRefreshTime } = require('../dashboard_snapshot_scheduler');

test('disk snapshots survive new store instances, retain true query time and never expire on reads', async () => {
  const directory = await fs.mkdtemp(path.join(os.tmpdir(), 'inventory-snapshot-test-'));
  try {
    const store = new DashboardSnapshotStore({ directory });
    await store.save('stock', { date: 'D' }, { ok: true, rows: [{ stock: 317 }], queried_at: 'source-time' });
    const restored = new DashboardSnapshotStore({ directory, now: () => new Date('2099-01-01') });
    const reads = await Promise.all(Array.from({ length: 30 }, () => restored.read('stock', { date: 'D' })));
    assert.ok(reads.every(result => result.rows[0].stock === 317 && result.queried_at === 'source-time' && result.snapshot.persisted));
    assert.equal(new Set(reads.map(result => result.snapshot.id)).size, 1);
    await fs.writeFile(store.file(store.key('stock', { date: 'D' })), '{}');
    assert.equal(await restored.read('stock', { date: 'D' }), null);
  } finally { await fs.rm(directory, { recursive: true, force: true }); }
});

test('concurrent explicit updates execute once; readers retain old batch during update; failure preserves disk', async () => {
  const directory = await fs.mkdtemp(path.join(os.tmpdir(), 'inventory-snapshot-test-'));
  try {
    const store = new DashboardSnapshotStore({ directory });
    await store.save('sales', {}, { ok: true, rows: [{ forecast: 1 }], queried_at: 'old' });
    const before = await store.read('sales', {});
    let finish, calls = 0;
    const load = () => { calls++; return new Promise(resolve => { finish = resolve; }); };
    const updates = Array.from({ length: 20 }, () => store.refresh('sales', {}, load));
    while (!finish) await new Promise(resolve => setImmediate(resolve));
    const during = await store.read('sales', {});
    assert.equal(during.rows[0].forecast, 1); assert.equal(during.snapshot.updating, true);
    finish({ ok: true, rows: [{ forecast: 2 }], queried_at: 'new' });
    const results = await Promise.all(updates);
    assert.equal(calls, 1); assert.ok(results.every(result => result.rows[0].forecast === 2));
    assert.notEqual(before.snapshot.id, results[0].snapshot.id);
    const pinned = await new DashboardSnapshotStore({ directory }).read('sales', {}, before.snapshot.id);
    assert.equal(pinned.rows[0].forecast, 1);
    assert.equal(pinned.queried_at, 'old');
    assert.equal(await store.read('sales', {}, '../invalid'), null);
    const file = store.file(store.key('sales', {}));
    const bytes = await fs.readFile(file);
    const failed = await store.refresh('sales', {}, () => { throw new Error('database unavailable'); });
    assert.equal(failed.rows[0].forecast, 2); assert.ok(failed.snapshot.last_error);
    assert.deepEqual(await fs.readFile(file), bytes);
    const incomplete = await store.refresh('sales', {}, () => ({ ok: false }));
    assert.equal(incomplete.queried_at, 'new');
  } finally { await fs.rm(directory, { recursive: true, force: true }); }
});

test('Beijing server update slots match frontend, including next-day/year boundary', () => {
  assert.equal(latestRefreshTime(Date.parse('2026-11-11T13:05:00+08:00')), Date.parse('2026-11-11T13:00:00+08:00'));
  assert.equal(latestRefreshTime(Date.parse('2027-01-01T08:00:00+08:00')), Date.parse('2026-12-31T21:00:00+08:00'));
});
