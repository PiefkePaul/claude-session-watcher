#!/usr/bin/env node

import { spawnSync } from "node:child_process";

const args = process.argv.slice(2);
const candidates = process.platform === "win32"
  ? ["py", "python", "python3"]
  : ["python3", "python"];

for (const python of candidates) {
  const probeArgs = python === "py" ? ["-m", "claude_session_watcher.cli", "--help"] : ["-m", "claude_session_watcher.cli", "--help"];
  const probe = spawnSync(python, probeArgs, { stdio: "ignore" });
  if (probe.status === 0) {
    const runArgs = python === "py" ? ["-m", "claude_session_watcher.cli", ...args] : ["-m", "claude_session_watcher.cli", ...args];
    const result = spawnSync(python, runArgs, { stdio: "inherit" });
    process.exit(result.status ?? 1);
  }
}

console.error("Could not find an installed Python module named claude_session_watcher.");
console.error("Install first with: pipx install claude-session-watcher");
process.exit(1);
