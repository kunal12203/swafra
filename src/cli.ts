#!/usr/bin/env node
/**
 * swafra CLI entry point (npm).
 * - `swafra` or `swafra stats` → delegates to Python CLI for stats
 * - `swafra serve` → starts the MCP server (Node)
 * - If Python swafra is available, prefer it for stats (single source of truth)
 */
import { spawnSync } from "child_process";

const args = process.argv.slice(2);
const cmd = args[0] || "stats";

if (cmd === "serve") {
  import("./index.js");
} else if (cmd === "stats" || args.length === 0) {
  // Try python3.12 first (3.14 is often broken on macOS), then python3
  const pythons = ["python3.12", "python3.11", "python3"];
  let success = false;
  for (const py of pythons) {
    const result = spawnSync(py, ["-m", "swafra.cli", ...args], {
      stdio: "inherit",
      env: process.env,
      timeout: 10000,
    });
    if (!result.error && result.status === 0) {
      success = true;
      break;
    }
    if (!result.error && result.status !== null) {
      // Python ran but swafra module not found — don't try other pythons
      break;
    }
  }

  if (!success) {
    console.log();
    console.log("  swafra (npm)");
    console.log("  Python package not found — install with: pip3 install swafra");
    console.log("  The stats dashboard requires the Python package.");
    console.log();
    console.log("  To start the MCP server: swafra serve");
    console.log();
  }
} else if (cmd === "help" || cmd === "-h" || cmd === "--help") {
  const pythons = ["python3.12", "python3.11", "python3"];
  let shown = false;
  for (const py of pythons) {
    const r = spawnSync(py, ["-m", "swafra.cli", "help"], {
      stdio: "inherit",
      env: process.env,
      timeout: 5000,
    });
    if (!r.error && r.status === 0) { shown = true; break; }
    if (!r.error && r.status !== null) break;
  }
  if (!shown) {
    console.log();
    console.log("  swafra — semantic memory for AI");
    console.log();
    console.log("  Usage:");
    console.log("    swafra              Show knowledge graph stats");
    console.log("    swafra stats        Same as above");
    console.log("    swafra serve        Start the MCP server");
    console.log("    swafra help         Show this help");
    console.log();
  }
} else {
  console.log(`  Unknown command: ${cmd}`);
  console.log("  Run `swafra help` for usage.");
  process.exit(1);
}
