#!/usr/bin/env node
/**
 * scimap MCP server — exposes Leiden-chunked knowledge graph operations as MCP tools
 * for Claude AI, ChatGPT, and any MCP-compatible client.
 *
 * Tools:
 *   - add_knowledge: Ingest text, Leiden-chunk it, embed, build knowledge graph
 *   - search_knowledge: Semantic vector search over all stored knowledge
 *   - graph_walk: Traverse the chunk knowledge graph from a starting point
 *   - get_context: Combined search + graph walk for maximum context retrieval
 *   - list_sources: List all ingested knowledge sources
 *   - delete_source: Remove a knowledge source and its chunks/edges
 */
import { Server } from "@modelcontextprotocol/sdk/server/index.js";
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import { CallToolRequestSchema, ListToolsRequestSchema, } from "@modelcontextprotocol/sdk/types.js";
import { Engine } from "./engine.js";
const engine = new Engine();
const server = new Server({
    name: "scimap",
    version: "0.1.0",
}, {
    capabilities: {
        tools: {},
    },
});
// --- Tool definitions ---
server.setRequestHandler(ListToolsRequestSchema, async () => ({
    tools: [
        {
            name: "add_knowledge",
            description: "Ingest text into the scimap knowledge graph. The text is split into semantically coherent chunks using the Leiden community detection algorithm, embedded locally, and connected with knowledge graph edges (sequential, similarity, community). Use this to store documents, notes, research papers, or any text you want to retrieve later.",
            inputSchema: {
                type: "object",
                properties: {
                    text: {
                        type: "string",
                        description: "The text content to ingest into the knowledge graph",
                    },
                    title: {
                        type: "string",
                        description: "A title/label for this knowledge source (for identification)",
                    },
                },
                required: ["text"],
            },
        },
        {
            name: "search_knowledge",
            description: "Semantic search over all stored knowledge. Returns the most relevant chunks ranked by cosine similarity to the query. Use this to find information you previously stored, or to check if you have relevant context before answering a question.",
            inputSchema: {
                type: "object",
                properties: {
                    query: {
                        type: "string",
                        description: "The search query (natural language)",
                    },
                    k: {
                        type: "number",
                        description: "Number of results to return (default: 8)",
                    },
                },
                required: ["query"],
            },
        },
        {
            name: "graph_walk",
            description: "Traverse the knowledge graph starting from a specific chunk. Follows sequential (next/prev) and similarity edges to find related chunks that may not appear in a direct search. Use this after a search hit to explore surrounding context.",
            inputSchema: {
                type: "object",
                properties: {
                    chunk_id: {
                        type: "string",
                        description: "The ID of the chunk to start walking from",
                    },
                    hops: {
                        type: "number",
                        description: "Maximum number of edge traversals (default: 2)",
                    },
                    k: {
                        type: "number",
                        description: "Maximum results to return (default: 10)",
                    },
                },
                required: ["chunk_id"],
            },
        },
        {
            name: "get_context",
            description: "Combined search + graph walk for maximum context retrieval. First searches for relevant chunks, then explores the graph around the top hit to gather surrounding context. This is the recommended way to retrieve knowledge — it combines the precision of search with the breadth of graph traversal.",
            inputSchema: {
                type: "object",
                properties: {
                    query: {
                        type: "string",
                        description: "What context you need (natural language)",
                    },
                    k: {
                        type: "number",
                        description: "Number of search results (default: 5)",
                    },
                    hops: {
                        type: "number",
                        description: "Graph walk depth from top hit (default: 1)",
                    },
                },
                required: ["query"],
            },
        },
        {
            name: "list_sources",
            description: "List all knowledge sources that have been ingested into scimap.",
            inputSchema: {
                type: "object",
                properties: {},
            },
        },
        {
            name: "delete_source",
            description: "Remove a knowledge source and all its chunks and graph edges.",
            inputSchema: {
                type: "object",
                properties: {
                    source_id: {
                        type: "string",
                        description: "The ID of the source to delete (from list_sources)",
                    },
                },
                required: ["source_id"],
            },
        },
    ],
}));
// --- Tool execution ---
server.setRequestHandler(CallToolRequestSchema, async (request) => {
    const { name, arguments: args } = request.params;
    try {
        let result;
        switch (name) {
            case "add_knowledge":
                result = await engine.call("add_knowledge", {
                    text: args.text,
                    title: args.title || "untitled",
                });
                break;
            case "search_knowledge":
                result = await engine.call("search", {
                    query: args.query,
                    k: args.k || 8,
                });
                break;
            case "graph_walk":
                result = await engine.call("graph_walk", {
                    chunk_id: args.chunk_id,
                    hops: args.hops || 2,
                    k: args.k || 10,
                });
                break;
            case "get_context":
                result = await engine.call("get_context", {
                    query: args.query,
                    k: args.k || 5,
                    hops: args.hops || 1,
                });
                break;
            case "list_sources":
                result = await engine.call("list_sources", {});
                break;
            case "delete_source":
                result = await engine.call("delete_source", {
                    source_id: args.source_id,
                });
                break;
            default:
                return {
                    content: [{ type: "text", text: `Unknown tool: ${name}` }],
                    isError: true,
                };
        }
        return {
            content: [
                {
                    type: "text",
                    text: JSON.stringify(result, null, 2),
                },
            ],
        };
    }
    catch (error) {
        const message = error instanceof Error ? error.message : String(error);
        return {
            content: [{ type: "text", text: `Error: ${message}` }],
            isError: true,
        };
    }
});
// --- Start ---
async function main() {
    await engine.start();
    const transport = new StdioServerTransport();
    await server.connect(transport);
    console.error("[scimap-mcp] server started");
}
main().catch((err) => {
    console.error("[scimap-mcp] fatal:", err);
    process.exit(1);
});
// Graceful shutdown
process.on("SIGINT", () => {
    engine.stop();
    process.exit(0);
});
process.on("SIGTERM", () => {
    engine.stop();
    process.exit(0);
});
