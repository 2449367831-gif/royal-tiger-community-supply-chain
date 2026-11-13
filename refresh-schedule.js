// Fixed Beijing-time refresh slots, independent of page-open/manual-refresh time.
(() => {
  'use strict';
  const HOUR = 3600000;
  const DAY = 24 * HOUR;
  const OFFSET = 8 * HOUR;
  const HOURS = [9, 13, 17, 21];

  function nextRefreshTime(now) {
    const dayStart = Math.floor((now + OFFSET) / DAY) * DAY - OFFSET;
    for (const hour of HOURS) {
      const slot = dayStart + hour * HOUR;
      if (slot > now) return slot;
    }
    return dayStart + DAY + HOURS[0] * HOUR;
  }

  function createSchedule({ now = Date.now, setTimer = setTimeout, clearTimer = clearTimeout } = {}) {
    const listeners = new Set();
    let due = nextRefreshTime(now());
    let timer;
    function arm() {
      clearTimer(timer);
      if (listeners.size) timer = setTimer(check, Math.max(1, due - now()));
    }
    function check() {
      const current = now();
      if (current >= due) {
        due = nextRefreshTime(current);
        const hour = Math.floor(((current + OFFSET) % DAY) / HOUR);
        // Resume a sleeping tab once, without replaying every missed slot or
        // starting an overnight refresh after the last evening slot.
        try {
          if (hour >= 9 && hour <= 21) for (const listener of listeners) listener();
        } finally { arm(); }
      } else arm();
    }
    return {
      check,
      subscribe(listener) {
        listeners.add(listener);
        if (listeners.size === 1) { due = nextRefreshTime(now()); arm(); }
        return () => { listeners.delete(listener); arm(); };
      },
    };
  }

  if (typeof module !== 'undefined' && module.exports) module.exports = { nextRefreshTime, createSchedule };
  if (typeof window !== 'undefined') {
    window.inventoryRefreshSchedule = createSchedule();
    window.addEventListener('focus', () => window.inventoryRefreshSchedule.check());
    document.addEventListener('visibilitychange', () => {
      if (!document.hidden) window.inventoryRefreshSchedule.check();
    });
  }
})();
