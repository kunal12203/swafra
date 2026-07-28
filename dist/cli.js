#!/usr/bin/env node
/**
 * swafra CLI — native Node.js stats dashboard.
 * Reads ~/.scimap/ JSON directly — no Python dependency needed.
 */
import { readFileSync, writeFileSync, statSync, existsSync, mkdirSync, chmodSync, unlinkSync, rmSync } from "fs";
import { join } from "path";
import { homedir } from "os";
const VERSION = "0.2.8";
const DATA_DIR = process.env.SCIMAP_DATA_DIR || join(homedir(), ".scimap");
function loadJson(filename) {
    const path = join(DATA_DIR, filename);
    if (!existsSync(path))
        return [];
    try {
        return JSON.parse(readFileSync(path, "utf-8"));
    }
    catch {
        return [];
    }
}
function fileSize(filename) {
    const path = join(DATA_DIR, filename);
    try {
        return statSync(path).size;
    }
    catch {
        return 0;
    }
}
function formatSize(bytes) {
    if (bytes < 1024)
        return `${bytes} B`;
    if (bytes < 1024 * 1024)
        return `${(bytes / 1024).toFixed(1)} KB`;
    return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}
function stats() {
    if (!existsSync(DATA_DIR)) {
        console.log("  No data found. Start by adding knowledge through an MCP client.");
        console.log(`  Data directory: ${DATA_DIR}`);
        return;
    }
    const chunks = loadJson("chunks.json");
    const edges = loadJson("edges.json");
    const sources = loadJson("sources.json");
    const facts = loadJson("facts.json");
    const activeChunks = chunks.filter(c => !c.superseded_by);
    const supersededChunks = chunks.filter(c => c.superseded_by);
    // Edge type breakdown
    const edgeTypes = {};
    for (const e of edges) {
        const t = e.type || "unknown";
        edgeTypes[t] = (edgeTypes[t] || 0) + 1;
    }
    // Community stats
    const communities = new Set(activeChunks.map(c => c.community_id));
    // Entity stats
    const entityCounts = {};
    for (const c of activeChunks) {
        for (const ent of c.entities || []) {
            entityCounts[ent] = (entityCounts[ent] || 0) + 1;
        }
    }
    // Token stats
    const totalTokens = activeChunks.reduce((sum, c) => sum + (c.token_count || 0), 0);
    // Chunk type breakdown
    const chunkTypes = {};
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
    const sourceChunks = {};
    for (const c of activeChunks) {
        const title = c.source_title || "unknown";
        sourceChunks[title] = (sourceChunks[title] || 0) + 1;
    }
    // LLM status
    let llmMode = "regex (fallback)";
    const configPath = join(DATA_DIR, "config.json");
    if (existsSync(configPath)) {
        try {
            const cfg = JSON.parse(readFileSync(configPath, "utf-8"));
            if (cfg.llm_provider && cfg.llm_api_key) {
                llmMode = `${cfg.llm_provider} (active)`;
            }
        }
        catch { }
    }
    else if (process.env.ANTHROPIC_API_KEY) {
        llmMode = "anthropic (env var)";
    }
    else if (process.env.OPENAI_API_KEY) {
        llmMode = "openai (env var)";
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
    console.log(`     Extraction:       ${llmMode}`);
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
function setup() {
    const claudeDir = join(homedir(), ".claude");
    const settingsPath = join(claudeDir, "settings.json");
    const hooksDir = join(claudeDir, "hooks");
    mkdirSync(hooksDir, { recursive: true });
    // Write hook scripts
    const postToolPath = join(hooksDir, "swafra-post-tool.sh");
    writeFileSync(postToolPath, `#!/bin/bash
MARKER_DIR="\${HOME}/.scimap/.session"
mkdir -p "$MARKER_DIR"
if [[ "$TOOL_NAME" == *"get_context"* ]]; then
    touch "$MARKER_DIR/retrieved"
fi
if [[ "$TOOL_NAME" == *"add_knowledge"* ]]; then
    touch "$MARKER_DIR/stored"
fi
exit 0
`);
    chmodSync(postToolPath, 0o755);
    const stopHookPath = join(hooksDir, "swafra-stop.sh");
    writeFileSync(stopHookPath, `#!/bin/bash
MARKER_DIR="\${HOME}/.scimap/.session"
if [ ! -f "\${HOME}/.scimap/chunks.json" ]; then
    rm -rf "$MARKER_DIR"
    exit 0
fi
CHUNKS_SIZE=$(stat -f%z "\${HOME}/.scimap/chunks.json" 2>/dev/null || stat -c%s "\${HOME}/.scimap/chunks.json" 2>/dev/null || echo "0")
if [ "$CHUNKS_SIZE" -lt 10 ]; then
    rm -rf "$MARKER_DIR"
    exit 0
fi
if [ ! -f "$MARKER_DIR/retrieved" ]; then
    echo "SWAFRA: You have persistent memory but did not call get_context. Call it now with the user's topic as the query." >&2
    rm -rf "$MARKER_DIR"
    exit 2
fi
rm -rf "$MARKER_DIR"
exit 0
`);
    chmodSync(stopHookPath, 0o755);
    // Update settings.json
    let settings = {};
    if (existsSync(settingsPath)) {
        try {
            settings = JSON.parse(readFileSync(settingsPath, "utf-8"));
        }
        catch { }
    }
    const hooks = settings.hooks = settings.hooks || {};
    // PostToolUse
    let postHooks = hooks.PostToolUse || [];
    postHooks = postHooks.filter((h) => !JSON.stringify(h).includes("swafra"));
    postHooks.push({
        matcher: "mcp__swafra",
        hooks: [{ type: "command", command: `bash ${postToolPath}`, timeout: 5 }]
    });
    hooks.PostToolUse = postHooks;
    // Stop
    let stopHooks = hooks.Stop || [];
    stopHooks = stopHooks.filter((h) => !JSON.stringify(h).includes("swafra"));
    stopHooks.push({
        matcher: "",
        hooks: [{ type: "command", command: `bash ${stopHookPath}`, timeout: 5 }]
    });
    hooks.Stop = stopHooks;
    writeFileSync(settingsPath, JSON.stringify(settings, null, 2) + "\n");
    // CLAUDE.md
    const claudeMdPath = join(claudeDir, "CLAUDE.md");
    const swafraBlock = `
# swafra Memory

You have persistent memory tools available via swafra MCP. Use them proactively.

## Rules
- ALWAYS call \`get_context\` at the start of every conversation with the user's topic
- ALWAYS call \`add_knowledge\` when the user shares preferences, decisions, context, or corrections
- Never say "I don't have context from previous sessions" without calling \`get_context\` first
- Use descriptive titles when storing: 'preference-editor', 'project-name', 'meeting-2026-07'
`;
    if (existsSync(claudeMdPath)) {
        const content = readFileSync(claudeMdPath, "utf-8");
        if (!content.toLowerCase().includes("swafra")) {
            writeFileSync(claudeMdPath, content.trimEnd() + "\n" + swafraBlock);
        }
    }
    else {
        writeFileSync(claudeMdPath, swafraBlock);
    }
    console.log();
    console.log("  \x1b[1;32m✓\x1b[0m swafra hooks installed!");
    console.log();
    console.log("  What was set up:");
    console.log(`    → Stop hook:        ${stopHookPath}`);
    console.log(`    → PostToolUse hook: ${postToolPath}`);
    console.log(`    → Settings:         ${settingsPath}`);
    console.log(`    → CLAUDE.md:        ${claudeMdPath}`);
    console.log();
    console.log("  Claude will now:");
    console.log("    • Always call get_context at session start");
    console.log("    • Proactively store knowledge without being asked");
    console.log("    • Get reminded if it forgets");
    console.log();
}
function remove(global) {
    const claudeDir = join(homedir(), ".claude");
    const settingsPath = join(claudeDir, "settings.json");
    const hooksDir = join(claudeDir, "hooks");
    // Remove hook scripts
    const removed = [];
    for (const f of ["swafra-post-tool.sh", "swafra-stop.sh"]) {
        const p = join(hooksDir, f);
        if (existsSync(p)) {
            unlinkSync(p);
            removed.push(p);
        }
    }
    // Remove from settings.json
    if (existsSync(settingsPath)) {
        const settings = JSON.parse(readFileSync(settingsPath, "utf-8"));
        const hooks = settings.hooks || {};
        for (const key of ["PostToolUse", "Stop"]) {
            if (hooks[key]) {
                hooks[key] = hooks[key].filter((h) => !JSON.stringify(h).includes("swafra"));
            }
        }
        writeFileSync(settingsPath, JSON.stringify(settings, null, 2) + "\n");
    }
    // Remove swafra block from CLAUDE.md
    const claudeMdPath = join(claudeDir, "CLAUDE.md");
    if (existsSync(claudeMdPath)) {
        let content = readFileSync(claudeMdPath, "utf-8");
        content = content.replace(/\n# swafra Memory\n[\s\S]*?(?=\n# |\s*$)/, "");
        writeFileSync(claudeMdPath, content.trim() + "\n");
    }
    // Clean session markers
    const sessionDir = join(homedir(), ".scimap", ".session");
    if (existsSync(sessionDir)) {
        rmSync(sessionDir, { recursive: true });
    }
    console.log();
    console.log("  \x1b[1;32m✓\x1b[0m swafra hooks removed!");
    console.log();
    for (const r of removed)
        console.log(`    → Deleted: ${r}`);
    console.log(`    → Cleaned: ${settingsPath}`);
    console.log(`    → Cleaned: ${claudeMdPath}`);
    if (global) {
        const dataDir = process.env.SCIMAP_DATA_DIR || join(homedir(), ".scimap");
        if (existsSync(dataDir)) {
            rmSync(dataDir, { recursive: true });
            console.log(`    → Deleted: ${dataDir} (all knowledge data)`);
        }
        console.log();
        console.log("  To also remove the MCP server:");
        console.log("    claude mcp remove swafra");
    }
    console.log();
}
function installSkill() {
    const skillDir = join(homedir(), ".claude", "skills", "swafra");
    mkdirSync(skillDir, { recursive: true });
    const skillContent = `# swafra — Persistent Memory Skill

You have persistent memory across sessions via swafra. Use it proactively — never wait for the user to ask.

## Commands

Run these via bash. All output is JSON.

### Retrieve memory (MANDATORY — call before your first response)

\`\`\`bash
python3 -m swafra.engine_cli get_context "USER_TOPIC_OR_QUESTION"
\`\`\`

### Store knowledge (call WITHOUT being asked)

\`\`\`bash
python3 -m swafra.engine_cli add "TEXT_TO_STORE" "descriptive-title"
\`\`\`

### Search memory

\`\`\`bash
python3 -m swafra.engine_cli search "query" 8
\`\`\`

### List all stored sources

\`\`\`bash
python3 -m swafra.engine_cli list
\`\`\`

### Delete a source

\`\`\`bash
python3 -m swafra.engine_cli delete "SOURCE_ID"
\`\`\`

### Graph walk (explore connections from a chunk)

\`\`\`bash
python3 -m swafra.engine_cli walk "CHUNK_ID" 2 10
\`\`\`

## Rules

1. **ALWAYS call \\\`get_context\\\` before your first response** — use the user's question/topic as the query
2. **ALWAYS call \\\`add\\\` when the user shares ANY of:**
   - Their name, role, preferences, or personal context
   - Project decisions, architecture, or technical choices
   - Corrections to your behavior or rules to follow
   - Documents, meeting notes, or long-form content
3. **Never say "I don't have context from previous sessions"** without calling \\\`get_context\\\` first
4. **Use descriptive titles** when storing: \\\`preference-editor\\\`, \\\`project-swafra\\\`, \\\`meeting-2026-07-28\\\`
5. **Err on the side of storing too much** — if it has future value, store it

## How it works

- Data is stored locally in \\\`~/.scimap/\\\` as JSON (no cloud, no database)
- Text is chunked using Leiden community detection, embedded locally, and graph-linked
- Retrieval uses hybrid scoring: BM25 + vector cosine + entity overlap + n-gram matching
- Facts are tracked with lifecycle management (old facts get superseded, stale chunks penalized)

## If python3 doesn't work

Try \\\`python3.12\\\` or \\\`python3.11\\\` instead.
`;
    writeFileSync(join(skillDir, "SKILL.md"), skillContent);
    console.log();
    console.log("  \x1b[1;32m✓\x1b[0m swafra skill installed!");
    console.log();
    console.log(`    → ${join(skillDir, "SKILL.md")}`);
    console.log();
    console.log("  Claude Code will now use swafra memory via bash commands.");
    console.log("  No MCP server needed — works directly through the skill.");
    console.log();
    console.log("  Note: Requires `pip install swafra` for the engine.");
    console.log();
}
function config(args) {
    const configPath = join(DATA_DIR, "config.json");
    mkdirSync(DATA_DIR, { recursive: true });
    if (args.length === 0) {
        if (existsSync(configPath)) {
            try {
                const cfg = JSON.parse(readFileSync(configPath, "utf-8"));
                const provider = cfg.llm_provider || "not set";
                const hasKey = cfg.llm_api_key ? "yes" : "no";
                const model = cfg.llm_model || "default";
                console.log();
                console.log("  \x1b[1;33m⚙ LLM Configuration\x1b[0m");
                console.log(`     Provider:   ${provider}`);
                console.log(`     Key set:    ${hasKey}`);
                console.log(`     Model:      ${model}`);
                if (cfg.llm_base_url)
                    console.log(`     Base URL:   ${cfg.llm_base_url}`);
                console.log();
            }
            catch {
                console.log("  Config file exists but is invalid.");
            }
        }
        else {
            console.log();
            console.log("  No LLM configured. Using regex fallback for extraction.");
            console.log();
            console.log("  Configure with:");
            console.log("    swafra config --provider anthropic --key sk-ant-...");
            console.log("    swafra config --provider openai --key sk-... --model gpt-4o-mini");
            console.log("    swafra config --provider openai-compatible --key KEY --url http://localhost:11434/v1 --model llama3");
            console.log();
            console.log("  Or set environment variables:");
            console.log("    ANTHROPIC_API_KEY=sk-ant-...");
            console.log("    OPENAI_API_KEY=sk-...");
            console.log("    SWAFRA_LLM_API_KEY=... + SWAFRA_LLM_BASE_URL=...");
            console.log();
        }
        return;
    }
    let provider = null;
    let apiKey = null;
    let baseUrl = null;
    let model = null;
    for (let i = 0; i < args.length; i++) {
        if ((args[i] === "--provider" || args[i] === "-p") && args[i + 1]) {
            provider = args[++i];
        }
        else if ((args[i] === "--key" || args[i] === "-k") && args[i + 1]) {
            apiKey = args[++i];
        }
        else if ((args[i] === "--url" || args[i] === "-u") && args[i + 1]) {
            baseUrl = args[++i];
        }
        else if ((args[i] === "--model" || args[i] === "-m") && args[i + 1]) {
            model = args[++i];
        }
        else if (args[i] === "--clear") {
            if (existsSync(configPath)) {
                const cfg = JSON.parse(readFileSync(configPath, "utf-8"));
                delete cfg.llm_provider;
                delete cfg.llm_api_key;
                delete cfg.llm_base_url;
                delete cfg.llm_model;
                writeFileSync(configPath, JSON.stringify(cfg, null, 2));
            }
            console.log("  \x1b[1;32m✓\x1b[0m LLM config cleared. Using regex fallback.");
            return;
        }
        else {
            console.log(`  Unknown option: ${args[i]}`);
            return;
        }
    }
    if (!provider || !apiKey) {
        console.log("  Both --provider and --key are required.");
        console.log("  Example: swafra config --provider anthropic --key sk-ant-...");
        return;
    }
    if (!["anthropic", "openai", "openai-compatible"].includes(provider)) {
        console.log(`  Unknown provider: ${provider}`);
        console.log("  Supported: anthropic, openai, openai-compatible");
        return;
    }
    let cfg = {};
    if (existsSync(configPath)) {
        try {
            cfg = JSON.parse(readFileSync(configPath, "utf-8"));
        }
        catch { }
    }
    cfg.llm_provider = provider;
    cfg.llm_api_key = apiKey;
    if (baseUrl)
        cfg.llm_base_url = baseUrl;
    else
        delete cfg.llm_base_url;
    if (model)
        cfg.llm_model = model;
    else
        delete cfg.llm_model;
    writeFileSync(configPath, JSON.stringify(cfg, null, 2));
    console.log();
    console.log("  \x1b[1;32m✓\x1b[0m LLM configured!");
    console.log(`     Provider: ${provider}`);
    console.log(`     Model:    ${model || "default"}`);
    if (baseUrl)
        console.log(`     Base URL: ${baseUrl}`);
    console.log();
    console.log("  swafra will now use LLM for:");
    console.log("    • Entity extraction (catches lowercase tech terms, tools, concepts)");
    console.log("    • Semantic dedup (skips storing duplicate knowledge)");
    console.log("    • Preference/topic detection");
    console.log();
    console.log("  Fallback: regex (when LLM call fails or times out)");
    console.log();
}
function main() {
    const args = process.argv.slice(2);
    const cmd = args[0] || "stats";
    if (cmd === "stats" || args.length === 0) {
        console.log();
        console.log(`  \x1b[1;37mswafra\x1b[0m v${VERSION}`);
        console.log("  Leiden-chunked, graph-linked semantic memory");
        stats();
    }
    else if (cmd === "serve") {
        import("./index.js");
    }
    else if (cmd === "setup") {
        setup();
    }
    else if (cmd === "skill") {
        installSkill();
    }
    else if (cmd === "config") {
        config(args.slice(1));
    }
    else if (cmd === "remove") {
        remove(args[1] === "global");
    }
    else if (cmd === "help" || cmd === "-h" || cmd === "--help") {
        console.log();
        console.log("  \x1b[1;37mswafra\x1b[0m — semantic memory for AI");
        console.log();
        console.log("  Usage:");
        console.log("    swafra              Show knowledge graph stats");
        console.log("    swafra stats        Same as above");
        console.log("    swafra serve        Start the MCP server (for MCP clients)");
        console.log("    swafra setup        Install enforcement hooks for Claude Code");
        console.log("    swafra skill        Install as Claude Code skill (no MCP needed)");
        console.log("    swafra config       Configure LLM for better extraction");
        console.log("    swafra remove       Disable hooks (keeps data)");
        console.log("    swafra remove global  Remove hooks + delete all stored data");
        console.log("    swafra help         Show this help");
        console.log();
    }
    else {
        console.log(`  Unknown command: ${cmd}`);
        console.log("  Run \`swafra help\` for usage.");
        process.exit(1);
    }
}
main();
