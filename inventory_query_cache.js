"use strict";

// Only successful results are shared. Query timestamps always remain those of
// the source response, so reuse cannot make old data appear freshly queried.
class InventoryQueryCache {
  constructor({ now = Date.now, ttl = 5 * 60 * 1000, maxBytes = 16 * 1024 * 1024 } = {}) {
    this.now = now;
    this.ttl = ttl;
    this.maxBytes = maxBytes;
    this.entries = new Map();
    this.pending = new Map();
    this.bytes = 0;
    this.generation = 0;
  }

  key(kind, parameters) { return `${kind}:${JSON.stringify(parameters)}`; }

  peek(kind, parameters) {
    const key = this.key(kind, parameters);
    const entry = this.entries.get(key);
    if (!entry) return null;
    if (entry.expires <= this.now()) {
      this.bytes -= entry.bytes;
      this.entries.delete(key);
      return null;
    }
    return entry.payload;
  }

  invalidateMovements() {
    this.generation += 1;
    for (const [key, entry] of this.entries) {
      if (key.startsWith('movements:')) {
        this.bytes -= entry.bytes;
        this.entries.delete(key);
      }
    }
  }

  invalidateStockDate(day) {
    for (const [key, entry] of this.entries) {
      if (key.startsWith('stock:') && entry.payload.stock_date === day) {
        this.bytes -= entry.bytes;
        this.entries.delete(key);
      }
    }
    this.invalidateMovements();
  }

  async get(kind, parameters, load, force = false) {
    const key = this.key(kind, parameters);
    const cached = !force && this.peek(kind, parameters);
    if (cached) return { ...cached, cache: { hit: true, ttl_seconds: this.ttl / 1000 } };
    if (this.pending.has(key)) return this.pending.get(key);
    const generation = this.generation;
    const pending = Promise.resolve().then(load).then(payload => {
      if (payload?.ok === true && Array.isArray(payload.rows) &&
          (kind !== 'movements' || generation === this.generation)) {
        const bytes = Buffer.byteLength(JSON.stringify(payload));
        const previous = this.entries.get(key);
        if (previous) { this.bytes -= previous.bytes; this.entries.delete(key); }
        if (bytes <= this.maxBytes) {
          while (this.entries.size && (this.bytes + bytes > this.maxBytes || this.entries.size >= 64)) {
            const oldest = this.entries.keys().next().value;
            this.bytes -= this.entries.get(oldest).bytes;
            this.entries.delete(oldest);
          }
          this.entries.set(key, { payload, bytes, expires: this.now() + this.ttl });
          this.bytes += bytes;
        }
      }
      return { ...payload, cache: { hit: false, ttl_seconds: this.ttl / 1000 } };
    }).finally(() => this.pending.delete(key));
    this.pending.set(key, pending);
    return pending;
  }
}

module.exports = { InventoryQueryCache };
