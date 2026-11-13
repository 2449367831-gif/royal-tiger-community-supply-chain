'use strict';

const chinaDay = value => new Date(new Date(value).getTime() + 8 * 3600000).toISOString().slice(0, 10);
const salesUpdatedAt = payload => payload?.sales_queried_at || payload?.queried_at;
const salesRefreshReady = (now = Date.now()) => {
  const china = new Date(new Date(now).getTime() + 8 * 3600000);
  return china.getUTCHours() * 60 + china.getUTCMinutes() >= 9 * 60 + 30;
};
const refreshPolicy = () => ({ timezone: 'Asia/Shanghai', inventory_hours: [9, 13, 17, 21], sales_daily_at: '09:30', sales_once_per_day: true });
function salesNeedsRefresh(payload, now = Date.now()) {
  const updated = Date.parse(salesUpdatedAt(payload));
  return !payload?.ok || !Number.isFinite(updated) || chinaDay(updated) < chinaDay(now) ||
    (chinaDay(updated) === chinaDay(now) && !salesRefreshReady(updated) && salesRefreshReady(now));
}
function withRefreshMetadata(payload, previous = null) {
  if (!payload.ok) return payload;
  return { ...payload,
    sales_queried_at: previous ? salesUpdatedAt(previous) : payload.queried_at,
    inventory_queried_at: payload.alerts?.transit_queried_at || payload.queried_at,
    refresh_mode: previous ? 'stock-only' : 'sales-and-stock',
    refresh_policy: refreshPolicy(),
  };
}
module.exports = { chinaDay, salesUpdatedAt, salesNeedsRefresh, salesRefreshReady, refreshPolicy, withRefreshMetadata };
