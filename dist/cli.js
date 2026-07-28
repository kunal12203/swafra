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
}
else if (cmd === "stats" || args.length === 0) {
    // Try Python swafra first
    const result = spawnSync("python3", ["-m", "swafra.cli", ...args], {
        stdio: "inherit",
        env: process.env,
    });
    if (result.status !== 0 && result.error) {
        // Python not available, show minimal info
        console.log();
        console.log("  swafra (npm)");
        console.log("  Python package not found — install with: pip install swafra");
        console.log("  The stats dashboard requires the Python package.");
        console.log();
        console.log("  To start the MCP server: swafra serve");
        console.log();
    }
}
else if (cmd === "help" || cmd === "-h" || cmd === "--help") {
    // Try Python help first for consistency
    const helpResult = spawnSync("python3", ["-m", "swafra.cli", "help"], {
        stdio: "inherit",
        env: process.env,
    });
    if (helpResult.status !== 0 && helpResult.error) {
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
}
else {
    console.log(`  Unknown command: ${cmd}`);
    console.log("  Run `swafra help` for usage.");
    process.exit(1);
}
