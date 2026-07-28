#!/usr/bin/env node
/**
 * swafra CLI — native Node.js stats dashboard.
 * Reads ~/.scimap/ JSON directly — no Python dependency needed.
 */
import { readFileSync, statSync, existsSync } from "fs";
import { join } from "path";
import { homedir } from "os";

const VERSION = "0.2.4";
const DATA_DIR = process.env.SCIMAP_DATA_DIR || join(homedir(), ".scimap");

function loadJson(filename: string): any[] {
  const path = join(DATA_DIR, filename);
  if (!existsSync(path)) return [];
  try {
    return JSON.parse(readFileSync(path, "utf-8"));
  } catch {
    return [];
  }
}

function fileSize(filename: string): number {
  const path = join(DATA_DIR, filename);
  try {
    return statSync(path).size;
  } catch {
    return 0;
  }
}

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function stats() {
  if (!existsSync(DATA_DIR)) {
    console.log("  No data found. Start by adding knowledge through an MCP client.");
    console.log(`  Data directory: ${DATA_DIR}`);
    return;
  }

  const chunks: any[] = loadJson("chunks.json");
  const edges: any[] = loadJson("edges.json");
  const sources: any[] = loadJson("sources.json");
  const facts: any[] = loadJson("facts.json");

  const activeChunks = chunks.filter(c => !c.superseded_by);
  const supersededChunks = chunks.filter(c => c.superseded_by);

  // Edge type breakdown
  const edgeTypes: Record<string, number> = {};
  for (const e of edges) {
    const t = e.type || "unknown";
    edgeTypes[t] = (edgeTypes[t] || 0) + 1;
  }

  // Community stats
  const communities = new Set(activeChunks.map(c => c.community_id));

  // Entity stats
  const entityCounts: Record<string, number> = {};
  for (const c of activeChunks) {
    for (const ent of c.entities || []) {
      entityCounts[ent] = (entityCounts[ent] || 0) + 1;
    }
  }

  // Token stats
  const totalTokens = activeChunks.reduce((sum, c) => sum + (c.token_count || 0), 0);

  // Chunk type breakdown
  const chunkTypes: Record<string, number> = {};
  for (const c of activeChunks) {
    const t = c.type || "unknown";
    chunkTypes[t] = (chunkTypes[t] || 0) + 1;
  }

  // Fact stats
  const activeFacts = facts.filter(f => f.valid_to === null || f.valid_to === undefined);
  const supersededFacts = facts.filter(f => f.valid_to !== null && f.valid_to !== undefined);

  // Cross-session edges
  const crossSession = edges.filter(e => e.type === "cross_session").length;

  // Storage size
  const totalSize = ["chunks.json", "edges.json", "sources.json", "facts.json"]
    .reduce((sum, f) => sum + fileSize(f), 0);

  // Source breakdown
  const sourceChunks: Record<string, number> = {};
  for (const c of activeChunks) {
    const title = c.source_title || "unknown";
    sourceChunks[title] = (sourceChunks[title] || 0) + 1;
  }

  // Print
  console.log();
  console.log("  \x1b[1;36m╭─────────────────────────────────────────╮\x1b[0m");
  console.log("  \x1b[1;36m│\x1b[0m       \x1b[1;37mswafra\x1b[0m knowledge graph stats       \x1b[1;36m│\x1b[0m");
  console.log("  \x1b[1;36m╰─────────────────────────────────────────╯\x1b[0m");
  console.log();

  // Overview
  console.log("  \x1b[1;33m⚡ Overview\x1b[0m");
  console.log(`     Sources:          ${sources.length}`);
  console.log(`     Active chunks:    ${activeChunks.length}`);
  console.log(`     Superseded:       ${supersededChunks.length}`);
  console.log(`     Edges:            ${edges.length}`);
  console.log(`     Total tokens:     ${totalTokens.toLocaleString()}`);
  console.log(`     Storage:          ${formatSize(totalSize)}`);
  console.log(`     Data dir:         ${DATA_DIR}`);
  console.log();

  // Graph topology
  console.log("  \x1b[1;33m🔗 Graph Topology\x1b[0m");
  console.log(`     Communities:      ${communities.size}`);
  console.log(`     Cross-session:    ${crossSession} edges`);
  if (Object.keys(edgeTypes).length > 0) {
    console.log("     Edge types:");
    const sorted = Object.entries(edgeTypes).sort((a, b) => b[1] - a[1]);
    for (const [type, count] of sorted) {
      console.log(`       ${type.padEnd(15)} ${count}`);
    }
  }
  console.log();

  // Facts
  console.log("  \x1b[1;33m📋 Facts\x1b[0m");
  console.log(`     Active:           ${activeFacts.length}`);
  console.log(`     Superseded:       ${supersededFacts.length}`);
  console.log(`     Total:            ${facts.length}`);
  console.log();

  // Entities
  console.log("  \x1b[1;33m🏷️  Entities\x1b[0m");
  console.log(`     Unique:           ${Object.keys(entityCounts).length}`);
  if (Object.keys(entityCounts).length > 0) {
    console.log("     Top entities:");
    const sorted = Object.entries(entityCounts).sort((a, b) => b[1] - a[1]).slice(0, 10);
    for (const [ent, count] of sorted) {
      console.log(`       ${ent.padEnd(20)} (${count} chunks)`);
    }
  }
  console.log();

  // Chunk types
  if (Object.keys(chunkTypes).length > 0) {
    console.log("  \x1b[1;33m📦 Chunk Types\x1b[0m");
    const sorted = Object.entries(chunkTypes).sort((a, b) => b[1] - a[1]);
    for (const [type, count] of sorted) {
      console.log(`     ${type.padEnd(18)} ${count}`);
    }
    console.log();
  }

  // Sources
  if (sources.length > 0) {
    console.log("  \x1b[1;33m📚 Sources\x1b[0m");
    const sorted = Object.entries(sourceChunks).sort((a, b) => b[1] - a[1]);
    for (const [title, count] of sorted.slice(0, 15)) {
      const display = title.length > 35 ? title.slice(0, 35) + "…" : title;
      console.log(`     ${display.padEnd(37)} ${count} chunks`);
    }
    if (sorted.length > 15) {
      console.log(`     ... and ${sorted.length - 15} more`);
    }
    console.log();
  }
}

function main() {
  const args = process.argv.slice(2);
  const cmd = args[0] || "stats";

  if (cmd === "stats" || args.length === 0) {
    console.log();
    console.log(`  \x1b[1;37mswafra\x1b[0m v${VERSION}`);
    console.log("  Leiden-chunked, graph-linked semantic memory");
    stats();
  } else if (cmd === "serve") {
    import("./index.js");
  } else if (cmd === "help" || cmd === "-h" || cmd === "--help") {
    console.log();
    console.log("  \x1b[1;37mswafra\x1b[0m — semantic memory for AI");
    console.log();
    console.log("  Usage:");
    console.log("    swafra              Show knowledge graph stats");
    console.log("    swafra stats        Same as above");
    console.log("    swafra serve        Start the MCP server");
    console.log("    swafra help         Show this help");
    console.log();
  } else {
    console.log(`  Unknown command: ${cmd}`);
    console.log("  Run \`swafra help\` for usage.");
    process.exit(1);
  }
}

main();
