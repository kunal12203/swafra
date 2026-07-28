/**
 * Python engine bridge — spawns the scimap_engine.py subprocess and communicates
 * via JSON-line protocol over stdin/stdout.
 */
import { spawn, execFileSync } from "node:child_process";
import { createInterface } from "node:readline";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
const __dirname = dirname(fileURLToPath(import.meta.url));
const ENGINE_PATH = join(__dirname, "..", "engine", "scimap_engine.py");
function findPython() {
    if (process.env.SCIMAP_PYTHON)
        return process.env.SCIMAP_PYTHON;
    const candidates = process.platform === "win32"
        ? ["python3.12", "python3.11", "python3.13", "python3", "python", "py"]
        : ["python3.12", "python3.11", "python3.13", "python3.10", "python3", "python"];
    for (const cmd of candidates) {
        try {
            const out = execFileSync(cmd, ["-c", "import sys, importlib; importlib.import_module('pip'); print(f'{sys.version_info.major}.{sys.version_info.minor}')"], {
                encoding: "utf8",
                timeout: 5000,
                stdio: ["ignore", "pipe", "pipe"],
            }).trim();
            const match = out.match(/^(\d+)\.(\d+)$/);
            if (match && parseInt(match[1]) >= 3 && parseInt(match[2]) >= 10) {
                return cmd;
            }
        }
        catch {
            // not found, not executable, or pip broken
        }
    }
    throw new Error("No suitable Python (>=3.10) with working pip found. Install Python 3.10+ or set SCIMAP_PYTHON env var.");
}
export class Engine {
    proc = null;
    pending = new Map();
    nextId = 1;
    ready = false;
    async start() {
        const pythonCmd = findPython();
        this.proc = spawn(pythonCmd, [ENGINE_PATH], {
            stdio: ["pipe", "pipe", "pipe"],
            env: { ...process.env },
        });
        this.proc.on("error", (err) => {
            console.error(`[scimap-engine] failed to start: ${err.message}`);
            console.error(`Make sure Python 3.12+ is installed and dependencies are available:\n` +
                `  pip install fastembed igraph leidenalg numpy`);
        });
        this.proc.on("exit", (code) => {
            if (code !== 0 && code !== null) {
                console.error(`[scimap-engine] exited with code ${code}`);
            }
            this.ready = false;
        });
        // Read stderr for logs
        if (this.proc.stderr) {
            const errRL = createInterface({ input: this.proc.stderr });
            errRL.on("line", (line) => {
                console.error(`[engine] ${line}`);
            });
        }
        // Read stdout for responses
        if (this.proc.stdout) {
            const outRL = createInterface({ input: this.proc.stdout });
            outRL.on("line", (line) => {
                try {
                    const resp = JSON.parse(line);
                    const id = resp.id;
                    const pending = this.pending.get(id);
                    if (pending) {
                        this.pending.delete(id);
                        if (resp.error) {
                            pending.reject(new Error(resp.error));
                        }
                        else {
                            pending.resolve(resp.result);
                        }
                    }
                }
                catch {
                    // ignore non-JSON lines
                }
            });
        }
        // Verify the engine is alive
        this.ready = true;
        try {
            await this.call("ping", {});
        }
        catch (e) {
            this.ready = false;
            throw new Error(`scimap engine failed to respond. Is Python installed with dependencies?\n` +
                `Run: pip install fastembed igraph leidenalg numpy`);
        }
    }
    async call(method, params) {
        if (!this.proc || !this.proc.stdin) {
            throw new Error("Engine not started");
        }
        const id = this.nextId++;
        const request = JSON.stringify({ id, method, params }) + "\n";
        return new Promise((resolve, reject) => {
            this.pending.set(id, { resolve, reject });
            const timeout = setTimeout(() => {
                this.pending.delete(id);
                reject(new Error(`Engine call ${method} timed out`));
            }, 60_000); // 60s timeout for heavy operations like embedding
            this.pending.set(id, {
                resolve: (value) => {
                    clearTimeout(timeout);
                    resolve(value);
                },
                reject: (reason) => {
                    clearTimeout(timeout);
                    reject(reason);
                },
            });
            this.proc.stdin.write(request);
        });
    }
    stop() {
        if (this.proc) {
            this.proc.kill();
            this.proc = null;
        }
        this.ready = false;
    }
}
