export declare class Engine {
    private proc;
    private pending;
    private nextId;
    private ready;
    start(): Promise<void>;
    call(method: string, params: Record<string, unknown>): Promise<unknown>;
    stop(): void;
}
