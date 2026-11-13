const fs = require('node:fs/promises');
const path = require('node:path');
const os = require('node:os');
const crypto = require('node:crypto');

const digest = text => crypto.createHash('sha256').update(text).digest('hex');
class DashboardSnapshotStore {
  constructor({ directory, now = () => new Date() } = {}) {
    this.directory = directory || process.env.INVENTORY_SNAPSHOT_DIR || path.join(process.env.LOCALAPPDATA || path.join(os.homedir(), '.local/share'), 'RoyalTigerInventory', 'dashboard-snapshots');
    this.now = now;
    this.pending = new Map();
    this.queued = new Set();
    this.failures = new Map();
  }
  key(kind, parameters) { return `${kind}:${JSON.stringify(parameters)}`; }
  file(key) { return path.join(this.directory, `${digest(key)}.json`); }
  versionFile(key, id) { return path.join(this.directory, 'versions', `${digest(key)}-${id}.json`); }
  async read(kind, parameters, version = null) {
    const key = this.key(kind, parameters);
    if (version && !/^[a-f0-9]{64}$/.test(version)) return null;
    try {
      const raw = await fs.readFile(version ? this.versionFile(key, version) : this.file(key), 'utf8');
      const record = JSON.parse(raw);
      if (record.version !== 1 || record.key !== key || record.sha256 !== digest(JSON.stringify(record.payload)) || (version && record.sha256 !== version) || !record.payload?.ok || !Array.isArray(record.payload.rows)) return null;
      return { ...record.payload, snapshot: { id: record.sha256, generated_at: record.generated_at,
        updating: this.pending.has(key) || this.queued.has(key), last_error: this.failures.get(key) || null, persisted: true },
        cache: { hit: true, mode: 'persistent_snapshot' } };
    } catch {
      if (version) { const current = await this.read(kind, parameters); return current?.snapshot.id === version ? current : null; }
      return null;
    }
  }
  async save(kind, parameters, payload, generatedAt) {
    const key = this.key(kind, parameters);
    const clean = { ...payload };
    delete clean.snapshot; delete clean.cache;
    const rawPayload = JSON.stringify(clean);
    const record = { version: 1, key, generated_at: generatedAt || this.now().toISOString(), sha256: digest(rawPayload), payload: clean };
    await fs.mkdir(this.directory, { recursive: true });
    await fs.mkdir(path.join(this.directory, 'versions'), { recursive: true });
    // Preserve the exact batch already shown in another user's open page.
    try {
      const prior = await fs.readFile(this.file(key), 'utf8');
      const old = JSON.parse(prior);
      if (old.key === key && /^[a-f0-9]{64}$/.test(old.sha256) && old.sha256 === digest(JSON.stringify(old.payload)))
        await fs.writeFile(this.versionFile(key, old.sha256), prior);
    } catch { /* First snapshot has no predecessor. */ }
    const temp = this.file(key) + `.${crypto.randomUUID()}.tmp`;
    try {
      await fs.writeFile(temp, JSON.stringify(record), { flag: 'wx' });
      await fs.copyFile(temp, this.versionFile(key, record.sha256));
      await fs.rename(temp, this.file(key));
    } finally { await fs.unlink(temp).catch(() => {}); }
    return this.read(kind, parameters);
  }
  async refresh(kind, parameters, load, validate = () => true) {
    const key = this.key(kind, parameters);
    if (this.pending.has(key)) {
      await this.pending.get(key);
      return await this.read(kind, parameters) || this.missing(kind, parameters);
    }
    const job = Promise.resolve().then(async () => {
      const previous = await this.read(kind, parameters);
      try {
        const payload = await load(previous);
        if (!payload?.ok || !Array.isArray(payload.rows) || !validate(payload, previous)) throw new Error('invalid_snapshot');
        await this.save(kind, parameters, payload);
        this.failures.delete(key);
      } catch {
        this.failures.set(key, previous ? '本次更新未成功，继续展示上次成功快照。' : '本次更新未成功，暂无可用快照。');
      }
    }).finally(() => this.pending.delete(key));
    this.pending.set(key, job);
    await job;
    return await this.read(kind, parameters) || this.missing(kind, parameters);
  }
  missing(kind, parameters) {
    const key = this.key(kind, parameters);
    const updating = this.pending.has(key) || this.queued.has(key);
    return { ok: false, code: 'SNAPSHOT_NOT_READY', error: updating ? '数据快照正在生成，请稍候。' : '该日期尚无数据快照，请点击“更新数据”生成。',
      snapshot: { updating, persisted: false, last_error: this.failures.get(key) || null } };
  }
}
const snapshotStore = new DashboardSnapshotStore();
module.exports = { DashboardSnapshotStore, snapshotStore };
