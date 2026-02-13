import React, { useEffect, useMemo, useState } from "react";
import { createRoot } from "react-dom/client";

import { DataTable, DataTableErrorBoundary } from "./components/tool-ui/data-table";
import type { Column, DataTableRowData } from "./components/tool-ui/data-table/types";

type SearchResult = {
  title?: string;
  type?: string;
  score?: number;
  permalink?: string;
  file_path?: string;
  metadata?: { tags?: string[] };
  tags?: string[];
};

type ToolOutput = {
  structuredContent?: unknown;
  structured_content?: unknown;
  structured?: unknown;
  content?: Array<{ type?: string; text?: string }>;
};

type RenderData = {
  toolInput?: { query?: string };
  toolOutput?: ToolOutput | string;
};

function safeJsonParse(text: string): unknown | null {
  try {
    return JSON.parse(text);
  } catch {
    return null;
  }
}

function extractStructured(toolOutput: ToolOutput | string | undefined): unknown {
  if (!toolOutput) return null;
  if (typeof toolOutput === "string") {
    return safeJsonParse(toolOutput) || { text: toolOutput };
  }

  const structured =
    toolOutput.structuredContent ||
    toolOutput.structured_content ||
    toolOutput.structured ||
    null;
  if (structured) return structured;

  const content = toolOutput.content;
  if (Array.isArray(content)) {
    const textBlock = content.find(
      (block) => block?.type === "text" && typeof block.text === "string",
    );
    if (textBlock?.text) {
      return safeJsonParse(textBlock.text) || { text: textBlock.text };
    }
  }

  return toolOutput;
}

function getResults(structured: unknown): SearchResult[] {
  if (!structured || typeof structured !== "object") return [];

  const record = structured as Record<string, unknown>;
  if (Array.isArray(record.results)) return record.results as SearchResult[];

  const nested = record.result as Record<string, unknown> | undefined;
  if (nested && Array.isArray(nested.results)) return nested.results as SearchResult[];

  return [];
}

function formatTags(result: SearchResult): string {
  const tags = result.metadata?.tags || result.tags || [];
  if (!Array.isArray(tags)) return "";
  return tags.join(", ");
}

function formatPath(result: SearchResult): string {
  return result.permalink || result.file_path || "";
}

const columns: Column<DataTableRowData>[] = [
  { key: "title", label: "Title", priority: "primary" },
  { key: "type", label: "Type", priority: "secondary" },
  { key: "score", label: "Score", align: "right", priority: "secondary" },
  { key: "path", label: "Path", priority: "tertiary" },
  { key: "tags", label: "Tags", priority: "tertiary" },
];

function SearchResultsApp() {
  const [query, setQuery] = useState<string>("Search results");
  const [rows, setRows] = useState<DataTableRowData[]>([]);
  const [hasData, setHasData] = useState(false);

  useEffect(() => {
    function handleMessage(event: MessageEvent) {
      const message = event.data as { type?: string; payload?: { renderData?: RenderData } };
      if (message?.type === "ui-lifecycle-iframe-render-data") {
        setHasData(true);
        const renderData = message.payload?.renderData;
        const nextQuery = renderData?.toolInput?.query;
        setQuery(nextQuery ? `Query: ${nextQuery}` : "Search results");

        const structured = extractStructured(renderData?.toolOutput);
        const results = getResults(structured);
        const nextRows = results.map((result, index) => ({
          id: result.permalink || result.file_path || result.title || `row-${index}`,
          title: result.title || "Untitled",
          type: result.type || "",
          score: typeof result.score === "number" ? Number(result.score.toFixed(2)) : 0,
          path: formatPath(result),
          tags: formatTags(result),
        }));
        setRows(nextRows);
      }
    }

    window.addEventListener("message", handleMessage);
    window.parent?.postMessage({ type: "ui-lifecycle-iframe-ready" }, "*");

    const timeout = setTimeout(() => {
      if (!hasData) {
        window.parent?.postMessage({ type: "ui-request-render-data" }, "*");
      }
    }, 200);

    return () => {
      window.removeEventListener("message", handleMessage);
      clearTimeout(timeout);
    };
  }, [hasData]);

  const footer = useMemo(() => {
    if (!rows.length) return "No results";
    return `${rows.length} result${rows.length === 1 ? "" : "s"}`;
  }, [rows.length]);

  return (
    <div className="min-h-screen bg-background text-foreground" data-theme="light">
      <div className="mx-auto flex max-w-5xl flex-col gap-4 px-6 py-6">
        <div className="flex items-center justify-between gap-4">
          <div>
            <h1 className="text-lg font-semibold">Search Results</h1>
            <p className="text-muted-foreground text-xs">{query}</p>
          </div>
          <span className="border-border bg-card text-foreground rounded-full border px-3 py-1 text-[11px]">
            tool-ui (React)
          </span>
        </div>

        <div className="border-border bg-card rounded-2xl border p-3 shadow-sm">
          <DataTableErrorBoundary>
            <DataTable
              id="basic-memory-search-results"
              rowIdKey="id"
              columns={columns}
              data={rows}
              emptyMessage="No results to show"
            />
          </DataTableErrorBoundary>
        </div>

        <div className="text-muted-foreground text-xs">{footer}</div>
      </div>
    </div>
  );
}

const root = document.getElementById("root");
if (root) {
  createRoot(root).render(<SearchResultsApp />);
}
