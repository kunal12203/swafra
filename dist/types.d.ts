export interface Chunk {
    chunk_id: string;
    content: string;
    source_title: string;
    score: number;
    community_id: number;
    entities: string[];
    type: string;
}
export interface WalkedChunk extends Chunk {
    distance: number;
    path_type: string;
}
export interface Source {
    id: string;
    title: string;
    chunks: number;
}
export interface AddResult {
    source_id: string;
    chunks: number;
    edges: number;
    facts?: {
        extracted: number;
        conflicts: number;
        superseded: number;
    };
    skipped?: boolean;
    reason?: string;
    duplicate_of?: string;
}
export interface DeleteResult {
    deleted_chunks: number;
}
