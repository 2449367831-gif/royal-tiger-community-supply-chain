const path = require("path");
const { execFile } = require("child_process");

function startInventoryFreezer() {
  let running = false;
  function check() {
    if (running) return;
    // The Windows task runs at 03:30. The server retries missing dates every
    // 15 minutes, and at startup after a missed task or interrupted session.
    running = true;
    const python = process.env.INVENTORY_PYTHON || (process.platform === "win32" ? "py" : "python3");
    execFile(python, ["-X", "utf8", path.join(__dirname, "scripts", "freeze_inventory_history.py"), "--due"], {
      cwd: __dirname, windowsHide: true, timeout: 20 * 60 * 1000, maxBuffer: 1024 * 1024,
      env: { ...process.env, PYTHONIOENCODING: "utf-8" },
    }, (error, stdout) => {
      running = false;
      // The CLI only emits safe metadata; credentials never enter this output.
      if (stdout.trim()) console.log(`[inventory-history] ${stdout.trim()}`);
      if (error) console.error("[inventory-history] Archive incomplete; will retry at the next check.");
    });
  }
  check();
  return setInterval(check, 15 * 60 * 1000).unref();
}

module.exports = { startInventoryFreezer };
