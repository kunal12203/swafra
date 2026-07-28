import type { AddResult, Chunk, DeleteResult, Source, WalkedChunk } from "./types.js";
export type { AddResult, Chunk, DeleteResult, Source, WalkedChunk };
export declare class Memory {
    private engine;
    private started;
    private _engine;
    /** Store text — chunked, embedded, and graph-linked. */
    add(text: string, opts?: {
        title?: string;
    }): Promise<AddResult>;
    /** Find relevant chunks by natural language query. */
    search(query: string, opts?: {
        k?: number;
    }): Promise<Chunk[]>;
    /** Search + graph walk combined — recommended for most use cases. */
    context(query: string, opts?: {
        k?: number;
        hops?: number;
    }): Promise<Chunk[]>;
    /** List all stored sources. */
    sources(): Promise<Source[]>;
    /** Remove a source and all its chunks/edges. */
    delete(sourceId: string): Promise<DeleteResult>;
    /** Explore connected chunks from a starting chunk. */
    walk(chunkId: string, opts?: {
        hops?: number;
        k?: number;
    }): Promise<WalkedChunk[]>;
    /** Shut down the underlying Python subprocess. */
    close(): void;
}
