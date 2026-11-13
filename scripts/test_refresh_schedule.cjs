const assert = require('node:assert/strict');
const { nextRefreshTime, createSchedule } = require('../refresh-schedule');
const at = time => Date.parse(time);
assert.equal(nextRefreshTime(at('2026-11-11T08:59:59+08:00')), at('2026-11-11T09:00:00+08:00'));
assert.equal(nextRefreshTime(at('2026-11-11T09:00:00+08:00')), at('2026-11-11T13:00:00+08:00'));
assert.equal(nextRefreshTime(at('2026-11-11T13:00:00+08:00')), at('2026-11-11T17:00:00+08:00'));
assert.equal(nextRefreshTime(at('2026-11-11T17:00:00+08:00')), at('2026-11-11T21:00:00+08:00'));
assert.equal(nextRefreshTime(at('2026-12-31T21:00:00+08:00')), at('2027-01-01T09:00:00+08:00'));
assert.equal(nextRefreshTime(at('2026-11-12T01:00:00+08:00')), at('2026-11-12T09:00:00+08:00'));

let current = at('2026-11-11T08:59:50+08:00');
let pending;
let inventory = 0, sales = 0;
const scheduler = createSchedule({ now: () => current,
  setTimer: (fn, delay) => { pending = { fn, at: current + delay }; return 1; },
  clearTimer: () => { pending = undefined; } });
const stopInventory = scheduler.subscribe(() => inventory++);
scheduler.subscribe(() => sales++);
assert.equal(pending.at, at('2026-11-11T09:00:00+08:00'));
for (const hour of [9,13,17,21]) {
  current = at(`2026-11-11T${String(hour).padStart(2,'0')}:00:00+08:00`);
  pending.fn();
}
assert.equal(inventory, 4); assert.equal(sales, 4);
assert.equal(pending.at, at('2026-11-12T09:00:00+08:00'));
current = at('2026-11-12T01:00:00+08:00');
scheduler.check(); assert.equal(sales, 4);
current = at('2026-11-12T17:05:00+08:00'); // Sleep over several slots: one catch-up.
scheduler.check(); scheduler.check(); assert.equal(sales, 5);
current = at('2026-11-12T22:05:00+08:00');
scheduler.check(); assert.equal(sales, 5); // No overnight catch-up.
assert.equal(pending.at, at('2026-11-13T09:00:00+08:00'));
stopInventory();
current = at('2026-11-13T09:00:00+08:00'); pending.fn();
assert.equal(inventory, 5); assert.equal(sales, 6);
console.log('Refresh schedule passed: Beijing slots, both modules, midnight/year boundary, sleep/resume, no overnight replay.');
