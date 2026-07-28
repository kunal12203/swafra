/**
 * swafra JS/TS SDK
 *
 * import { Memory } from 'swafra'
 * const m = new Memory()
 * await m.add("I use VS Code", { title: "prefs" })
 * await m.search("editor")
 * await m.context("what editor do I prefer?")
 */
import { Engine } from "./engine.js";
export class Memory {
    engine;
    started = false;
    async _engine() {
        if (!this.started) {
            this.engine = new Engine();
            await this.engine.start();
            this.started = true;
        }
        return this.engine;
    }
    /** Store text — chunked, embedded, and graph-linked. */
    async add(text, opts) {
        const e = await this._engine();
        return e.call("add_knowledge", {
            text,
            title: opts?.title ?? "untitled",
        });
    }
    /** Find relevant chunks by natural language query. */
    async search(query, opts) {
        const e = await this._engine();
        return e.call("search", { query, k: opts?.k ?? 8 });
    }
    /** Search + graph walk combined — recommended for most use cases. */
    async context(query, opts) {
        const e = await this._engine();
        return e.call("get_context", {
            query,
            k: opts?.k ?? 5,
            hops: opts?.hops ?? 1,
        });
    }
    /** List all stored sources. */
    async sources() {
        const e = await this._engine();
        return e.call("list_sources", {});
    }
    /** Remove a source and all its chunks/edges. */
    async delete(sourceId) {
        const e = await this._engine();
        return e.call("delete_source", { source_id: sourceId });
    }
    /** Explore connected chunks from a starting chunk. */
    async walk(chunkId, opts) {
        const e = await this._engine();
        return e.call("graph_walk", {
            chunk_id: chunkId,
            hops: opts?.hops ?? 2,
            k: opts?.k ?? 10,
        });
    }
    /** Shut down the underlying Python subprocess. */
    close() {
        if (this.started) {
            this.engine.stop();
            this.started = false;
        }
    }
}
