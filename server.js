const crypto = require("crypto");
const http = require("http");
const fs = require("fs");
const path = require("path");
const { queryInventory } = require("./inventory_api");
const { queryDailySales } = require("./daily_sales_api");

const ROOT = __dirname;
const HOST = process.env.PICKUP_WEB_HOST || "0.0.0.0";
const PORT = Number(process.env.PICKUP_WEB_PORT || 8788);

const AUTH_ENABLED = process.env.PICKUP_AUTH_ENABLED === "1";
const AUTH_SECRET = process.env.PICKUP_AUTH_SECRET || "";
const AUTH_USERS_FILE = process.env.PICKUP_AUTH_USERS_FILE || path.join(ROOT, "config", "users.json");
const AUTH_MAX_AGE_SECONDS = Number(process.env.PICKUP_AUTH_MAX_AGE_SECONDS || 12 * 60 * 60);
const AUTH_COOKIE_NAME = "pickup_session";
const AUTH_INTERNAL_CIDRS = (process.env.PICKUP_AUTH_INTERNAL_CIDRS ||
  "127.0.0.1/32,10.0.0.0/8,172.16.0.0/12,192.168.0.0/16")
  .split(",")
  .map((value) => value.trim())
  .filter(Boolean);
const AUTH_INTERNAL_HOSTS = (process.env.PICKUP_AUTH_INTERNAL_HOSTS ||
  "192.168.100.78,127.0.0.1,localhost")
  .split(",")
  .map((value) => value.trim().toLowerCase())
  .filter(Boolean);

const MIME_TYPES = {
  ".html": "text/html; charset=utf-8",
  ".css": "text/css; charset=utf-8",
  ".js": "application/javascript; charset=utf-8",
  ".json": "application/json; charset=utf-8",
  ".png": "image/png",
  ".jpg": "image/jpeg",
  ".jpeg": "image/jpeg",
  ".ico": "image/x-icon",
};

function sendJson(response, status, payload) {
  response.writeHead(status, {
    "Content-Type": "application/json; charset=utf-8",
    "Cache-Control": "no-store",
  });
  response.end(JSON.stringify(payload, null, 2));
}

function sendText(response, status, text) {
  response.writeHead(status, {
    "Content-Type": "text/plain; charset=utf-8",
    "Cache-Control": "no-store",
  });
  response.end(text);
}

function sendRedirect(response, location) {
  response.writeHead(302, {
    Location: location,
    "Cache-Control": "no-store",
  });
  response.end();
}

function timingSafeEqualText(left, right) {
  const leftBuffer = Buffer.from(String(left));
  const rightBuffer = Buffer.from(String(right));
  if (leftBuffer.length !== rightBuffer.length) {
    return false;
  }
  return crypto.timingSafeEqual(leftBuffer, rightBuffer);
}

function parseCookies(request) {
  const cookies = {};
  const header = request.headers.cookie || "";
  for (const part of header.split(";")) {
    const index = part.indexOf("=");
    if (index === -1) {
      continue;
    }
    const key = part.slice(0, index).trim();
    const value = part.slice(index + 1).trim();
    if (key) {
      cookies[key] = decodeURIComponent(value);
    }
  }
  return cookies;
}

function signSession(username, expiresAt) {
  const payload = Buffer.from(JSON.stringify({ username, expiresAt })).toString("base64url");
  const signature = crypto.createHmac("sha256", AUTH_SECRET).update(payload).digest("base64url");
  return `${payload}.${signature}`;
}

function verifySession(value) {
  if (!AUTH_SECRET || !value) {
    return false;
  }
  const parts = value.split(".");
  if (parts.length !== 2) {
    return false;
  }
  const [payload, signature] = parts;
  const expected = crypto.createHmac("sha256", AUTH_SECRET).update(payload).digest("base64url");
  if (!timingSafeEqualText(signature, expected)) {
    return false;
  }
  try {
    const session = JSON.parse(Buffer.from(payload, "base64url").toString("utf8"));
    return Boolean(findUser(session.username)) && Number(session.expiresAt) > Date.now();
  } catch {
    return false;
  }
}

function loadUsers() {
  try {
    const payload = JSON.parse(fs.readFileSync(AUTH_USERS_FILE, "utf8"));
    if (!Array.isArray(payload.users)) {
      return [];
    }
    return payload.users.filter((user) => user && user.enabled !== false && user.username);
  } catch {
    return [];
  }
}

function authDiagnostics() {
  return {
    users_file: AUTH_USERS_FILE,
    users_file_exists: fs.existsSync(AUTH_USERS_FILE),
    users_count: loadUsers().length,
    secret_configured: Boolean(AUTH_SECRET),
    internal_hosts: AUTH_INTERNAL_HOSTS,
  };
}

function findUser(username) {
  return loadUsers().find((user) => user.username === username);
}

function verifyPassword(user, password) {
  if (!user) {
    return false;
  }
  if (user.algorithm === "pbkdf2-sha256") {
    const iterations = Number(user.iterations || 120000);
    const keyLength = Number(user.keyLength || 32);
    const salt = Buffer.from(String(user.salt || ""), "base64");
    const expected = Buffer.from(String(user.passwordHash || ""), "base64");
    if (!salt.length || !expected.length) {
      return false;
    }
    const actual = crypto.pbkdf2Sync(String(password), salt, iterations, keyLength, "sha256");
    if (actual.length !== expected.length) {
      return false;
    }
    return crypto.timingSafeEqual(actual, expected);
  }

  if (typeof user.password === "string") {
    return timingSafeEqualText(password, user.password);
  }

  return false;
}

function authCookie(value, maxAgeSeconds) {
  const parts = [
    `${AUTH_COOKIE_NAME}=${encodeURIComponent(value)}`,
    "Path=/",
    "HttpOnly",
    "SameSite=Lax",
    `Max-Age=${maxAgeSeconds}`,
  ];
  if (process.env.PICKUP_AUTH_COOKIE_SECURE === "1") {
    parts.push("Secure");
  }
  return parts.join("; ");
}

function isAuthenticated(request) {
  if (!AUTH_ENABLED) {
    return true;
  }
  if (isInternalRequest(request)) {
    return true;
  }
  return verifySession(parseCookies(request)[AUTH_COOKIE_NAME]);
}

function normalizeIp(value) {
  if (!value) {
    return "";
  }
  const cleaned = String(value).trim();
  if (cleaned.startsWith("::ffff:")) {
    return cleaned.slice(7);
  }
  if (cleaned === "::1") {
    return "127.0.0.1";
  }
  return cleaned;
}

function ipv4ToNumber(ip) {
  const parts = ip.split(".");
  if (parts.length !== 4) {
    return null;
  }
  let result = 0;
  for (const part of parts) {
    if (!/^\d+$/.test(part)) {
      return null;
    }
    const number = Number(part);
    if (number < 0 || number > 255) {
      return null;
    }
    result = (result << 8) + number;
  }
  return result >>> 0;
}

function cidrContains(cidr, ip) {
  const [base, prefixText = "32"] = cidr.split("/");
  const baseNumber = ipv4ToNumber(base);
  const ipNumber = ipv4ToNumber(ip);
  const prefix = Number(prefixText);
  if (baseNumber === null || ipNumber === null || prefix < 0 || prefix > 32) {
    return false;
  }
  const mask = prefix === 0 ? 0 : (0xffffffff << (32 - prefix)) >>> 0;
  return (baseNumber & mask) === (ipNumber & mask);
}

function getHostName(request) {
  return String(request.headers.host || "").split(":")[0].toLowerCase();
}

function getRemoteIp(request) {
  return normalizeIp(request.socket.remoteAddress || request.connection?.remoteAddress || "");
}

function isInternalRequest(request) {
  const host = getHostName(request);
  const remoteIp = getRemoteIp(request);
  const hostAllowed = AUTH_INTERNAL_HOSTS.includes(host);
  const ipAllowed = AUTH_INTERNAL_CIDRS.some((cidr) => cidrContains(cidr, remoteIp));
  return hostAllowed && ipAllowed;
}

function sendLoginPage(request, response) {
  const currentUrl = new URL(request.url || "/login", `http://${request.headers.host || "localhost"}`);
  const hasError = currentUrl.searchParams.has("error");
  const html = `<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>登录 - 线下可提货数据</title>
  <style>
    :root { --yellow: #fabe00; --brown: #632c16; --red: #cc0000; --muted: #7f6b55; }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      min-height: 100vh;
      display: grid;
      place-items: center;
      padding: 24px;
      color: #2a170d;
      font-family: "Microsoft YaHei", "Segoe UI", Arial, sans-serif;
      background: radial-gradient(circle at 12% 0%, rgba(250, 190, 0, .25), transparent 32%),
        linear-gradient(180deg, #fff8df 0%, #fffdf7 48%, #fff 100%);
    }
    .login-card {
      width: min(420px, 100%);
      border: 1px solid rgba(99, 44, 22, .12);
      border-radius: 10px;
      background: rgba(255, 255, 255, .94);
      box-shadow: 0 24px 70px rgba(99, 44, 22, .16);
      overflow: hidden;
    }
    .login-head {
      padding: 26px 28px 22px;
      background: linear-gradient(135deg, var(--yellow), #ffd84a);
      color: var(--brown);
    }
    .login-head span { display: block; font-size: 13px; font-weight: 800; color: rgba(99, 44, 22, .78); }
    .login-head strong { display: block; margin-top: 4px; font-size: 26px; line-height: 1.15; font-weight: 900; }
    form { padding: 26px 28px 28px; }
    label { display: block; margin-bottom: 16px; font-size: 13px; font-weight: 800; color: var(--brown); }
    input {
      width: 100%;
      height: 40px;
      margin-top: 7px;
      padding: 0 12px;
      border: 1px solid #d9cbb6;
      border-radius: 5px;
      color: #2a170d;
      font: inherit;
      outline: none;
    }
    input:focus { border-color: var(--yellow); box-shadow: 0 0 0 3px rgba(250, 190, 0, .22); }
    button {
      width: 100%;
      height: 42px;
      border: 0;
      border-radius: 5px;
      background: var(--yellow);
      color: var(--brown);
      font: inherit;
      font-weight: 900;
      cursor: pointer;
      box-shadow: 0 12px 22px rgba(250, 190, 0, .25);
    }
    .error {
      margin: 0 0 16px;
      padding: 10px 12px;
      border-radius: 5px;
      background: #fff0f0;
      color: var(--red);
      font-size: 13px;
      font-weight: 800;
    }
    .hint { margin: 14px 0 0; color: var(--muted); font-size: 12px; line-height: 1.5; }
  </style>
</head>
<body>
  <section class="login-card" aria-label="登录">
    <div class="login-head">
      <span>皇家小虎</span>
      <strong>线下可提货数据</strong>
    </div>
    <form method="post" action="/login">
      ${hasError ? '<p class="error">账号或密码错误，请重新输入。</p>' : ""}
      <label>账号
        <input name="username" autocomplete="username" required autofocus>
      </label>
      <label>密码
        <input name="password" type="password" autocomplete="current-password" required>
      </label>
      <button type="submit">登录</button>
      <p class="hint">公共电脑使用完请关闭浏览器；会话默认保留 12 小时。</p>
    </form>
  </section>
</body>
</html>`;

  response.writeHead(200, {
    "Content-Type": "text/html; charset=utf-8",
    "Cache-Control": "no-store",
  });
  response.end(html);
}

function readRequestBody(request, limitBytes = 10 * 1024) {
  return new Promise((resolve, reject) => {
    let body = "";
    request.on("data", (chunk) => {
      body += chunk;
      if (Buffer.byteLength(body) > limitBytes) {
        reject(new Error("request body too large"));
        request.destroy();
      }
    });
    request.on("end", () => resolve(body));
    request.on("error", reject);
  });
}

function safeResolve(urlPath) {
  const decoded = decodeURIComponent(urlPath.split("?")[0]);
  const cleanPath = decoded === "/" ? "/index.html" : decoded;
  const filePath = path.normalize(path.join(ROOT, cleanPath));
  if (!filePath.startsWith(ROOT)) {
    return null;
  }
  return filePath;
}

function readCurrentMeta() {
  const dataPath = path.join(ROOT, "data", "pickup-data.json");
  try {
    const payload = JSON.parse(fs.readFileSync(dataPath, "utf8"));
    return {
      ok: true,
      updated_at: payload.updated_at || null,
      note: payload.note || null,
      row_count: Array.isArray(payload.rows) ? payload.rows.length : 0,
    };
  } catch (error) {
    return {
      ok: false,
      error: error.message,
      row_count: 0,
    };
  }
}

async function handleLoginPost(request, response) {
  if (!AUTH_ENABLED) {
    sendRedirect(response, "/");
    return;
  }
  if (!AUTH_SECRET || loadUsers().length === 0) {
    sendText(response, 500, "Authentication is enabled but users or secret are missing.");
    return;
  }
  try {
    const body = await readRequestBody(request);
    const form = new URLSearchParams(body);
    const username = form.get("username") || "";
    const password = form.get("password") || "";
    const user = findUser(username);
    if (!verifyPassword(user, password)) {
      sendRedirect(response, "/login?error=1");
      return;
    }
    const expiresAt = Date.now() + AUTH_MAX_AGE_SECONDS * 1000;
    response.writeHead(302, {
      Location: "/",
      "Set-Cookie": authCookie(signSession(username, expiresAt), AUTH_MAX_AGE_SECONDS),
      "Cache-Control": "no-store",
    });
    response.end();
  } catch (error) {
    sendText(response, 400, error.message);
  }
}

function serveStatic(request, response) {
  const filePath = safeResolve(request.url || "/");
  if (!filePath) {
    response.writeHead(403);
    response.end("Forbidden");
    return;
  }

  fs.stat(filePath, (statError, stat) => {
    if (statError || !stat.isFile()) {
      response.writeHead(404, { "Content-Type": "text/plain; charset=utf-8" });
      response.end("Not Found");
      return;
    }

    const ext = path.extname(filePath).toLowerCase();
    const headers = {
      "Content-Type": MIME_TYPES[ext] || "application/octet-stream",
    };
    if (
      filePath.endsWith(path.join("data", "pickup-data.json")) ||
      filePath.endsWith(path.join("data", "dashboard-data.json")) ||
      filePath.endsWith(path.join("data", "dashboard-data.js"))
    ) {
      headers["Cache-Control"] = "no-store";
    } else {
      headers["Cache-Control"] = "public, max-age=300";
    }

    response.writeHead(200, headers);
    if (request.method === "HEAD") {
      response.end();
      return;
    }

    fs.readFile(filePath, (readError, body) => {
      if (readError) {
        response.destroy(readError);
        return;
      }
      response.end(body);
    });
  });
}

const server = http.createServer((request, response) => {
  const urlPath = (request.url || "/").split("?")[0];

  if (request.method === "GET" && urlPath === "/health") {
    sendJson(response, 200, {
      service: "offline-pickup-data",
      status: "ok",
      auth_enabled: AUTH_ENABLED,
      auth: AUTH_ENABLED ? authDiagnostics() : undefined,
      ...readCurrentMeta(),
    });
    return;
  }

  if (AUTH_ENABLED && request.method === "GET" && urlPath === "/login") {
    sendLoginPage(request, response);
    return;
  }

  if (AUTH_ENABLED && request.method === "POST" && urlPath === "/login") {
    handleLoginPost(request, response);
    return;
  }

  if (AUTH_ENABLED && request.method === "GET" && urlPath === "/logout") {
    response.writeHead(302, {
      Location: "/login",
      "Set-Cookie": authCookie("", 0),
      "Cache-Control": "no-store",
    });
    response.end();
    return;
  }

  if (request.method !== "GET" && request.method !== "HEAD") {
    response.writeHead(405, { Allow: "GET, HEAD" });
    response.end();
    return;
  }

  if (!isAuthenticated(request)) {
    if (urlPath === "/meta" || urlPath.startsWith("/data/") || urlPath.startsWith("/api/")) {
      sendJson(response, 401, { ok: false, error: "unauthorized" });
      return;
    }
    sendRedirect(response, "/login");
    return;
  }

  if (urlPath === "/meta") {
    sendJson(response, 200, readCurrentMeta());
    return;
  }

  if (urlPath === "/api/future-sales") {
    if (request.method !== "GET") { sendJson(response, 405, {ok:false,error:"请使用 GET 查询预测。"}); return; }
    require("./future_sales_api").queryFutureSales(request, response, sendJson);
    return;
  }

  if (urlPath === "/api/daily-sales") {
    if (request.method !== "GET") { sendJson(response, 405, { ok: false, error: "请使用 GET 查询销量。" }); return; }
    queryDailySales(request, response, sendJson);
    return;
  }

  if (urlPath === "/api/inventory" || urlPath === "/api/inventory/movements") {
    if (request.method !== "GET") {
      sendJson(response, 405, { ok: false, code: "method_not_allowed", error: "请使用 GET 查询库存。" });
      return;
    }
    queryInventory(request, response, sendJson);
    return;
  }

  serveStatic(request, response);
});

server.listen(PORT, HOST, () => {
  console.log(`Offline pickup data server listening on http://${HOST}:${PORT}`);
  console.log(`Authentication ${AUTH_ENABLED ? "enabled" : "disabled"}`);
  if (PORT === 8791) {
    require("./inventory_freeze_scheduler").startInventoryFreezer();
    require("./dashboard_snapshot_scheduler").startDashboardSnapshots();
  }
});
