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
import type { AddResult, Chunk, DeleteResult, Source, WalkedChunk } from "./types.js";

export type { AddResult, Chunk, DeleteResult, Source, WalkedChunk };

export class Memory {
  private engine!: Engine;
  private started = false;

  private async _engine(): Promise<Engine> {
    if (!this.started) {
      this.engine = new Engine();
      await this.engine.start();
      this.started = true;
    }
    return this.engine;
  }

  /** Store text — chunked, embedded, and graph-linked. */
  async add(text: string, opts?: { title?: string }): Promise<AddResult> {
    const e = await this._engine();
    return e.call("add_knowledge", {
      text,
      title: opts?.title ?? "untitled",
    }) as Promise<AddResult>;
  }

  /** Find relevant chunks by natural language query. */
  async search(query: string, opts?: { k?: number }): Promise<Chunk[]> {
    const e = await this._engine();
    return e.call("search", { query, k: opts?.k ?? 8 }) as Promise<Chunk[]>;
  }

  /** Search + graph walk combined — recommended for most use cases. */
  async context(query: string, opts?: { k?: number; hops?: number }): Promise<Chunk[]> {
    const e = await this._engine();
    return e.call("get_context", {
      query,
      k: opts?.k ?? 5,
      hops: opts?.hops ?? 1,
    }) as Promise<Chunk[]>;
  }

  /** List all stored sources. */
  async sources(): Promise<Source[]> {
    const e = await this._engine();
    return e.call("list_sources", {}) as Promise<Source[]>;
  }

  /** Remove a source and all its chunks/edges. */
  async delete(sourceId: string): Promise<DeleteResult> {
    const e = await this._engine();
    return e.call("delete_source", { source_id: sourceId }) as Promise<DeleteResult>;
  }

  /** Explore connected chunks from a starting chunk. */
  async walk(chunkId: string, opts?: { hops?: number; k?: number }): Promise<WalkedChunk[]> {
    const e = await this._engine();
    return e.call("graph_walk", {
      chunk_id: chunkId,
      hops: opts?.hops ?? 2,
      k: opts?.k ?? 10,
    }) as Promise<WalkedChunk[]>;
  }

  /** Shut down the underlying Python subprocess. */
  close(): void {
    if (this.started) {
      this.engine.stop();
      this.started = false;
    }
  }
}
