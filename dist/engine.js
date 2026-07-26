/**
 * Python engine bridge — spawns the scimap_engine.py subprocess and communicates
 * via JSON-line protocol over stdin/stdout.
 */
import { spawn } from "node:child_process";
import { createInterface } from "node:readline";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
const __dirname = dirname(fileURLToPath(import.meta.url));
const ENGINE_PATH = join(__dirname, "..", "engine", "scimap_engine.py");
export class Engine {
    proc = null;
    pending = new Map();
    nextId = 1;
    ready = false;
    async start() {
        const pythonCmd = process.env.SCIMAP_PYTHON || "python3";
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
