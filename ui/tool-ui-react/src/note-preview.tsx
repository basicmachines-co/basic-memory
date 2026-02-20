import React, { useEffect, useState } from "react";
import { createRoot } from "react-dom/client";

import { Badge } from "./components/ui/badge";

type ToolOutput = {
  content?: Array<{ type?: string; text?: string }>;
  structuredContent?: unknown;
  structured_content?: unknown;
  structured?: unknown;
};

type RenderData = {
  toolInput?: { identifier?: string };
  toolOutput?: ToolOutput | string;
};

function stripFrontmatter(text: string): string {
  if (!text.startsWith("---")) return text;
  const end = text.indexOf("---", 3);
  if (end === -1) return text;
  return text.slice(end + 3).trim();
}

function parseTitle(text: string): string | null {
  const lines = text.split("\n");
  for (const line of lines) {
    if (line.startsWith("# ")) {
      return line.replace("# ", "").trim();
    }
  }
  return null;
}

function extractText(toolOutput: ToolOutput | string | undefined): string {
  if (!toolOutput) return "";
  if (typeof toolOutput === "string") return toolOutput;

  const content = toolOutput.content;
  if (Array.isArray(content)) {
    const textBlock = content.find(
      (block) => block?.type === "text" && typeof block.text === "string",
    );
    if (textBlock?.text) return textBlock.text;
  }

  if (typeof toolOutput.structuredContent === "string") {
    return toolOutput.structuredContent;
  }

  return "";
}

function NotePreviewApp() {
  const [identifier, setIdentifier] = useState<string>("");
  const [title, setTitle] = useState<string>("Note Preview");
  const [content, setContent] = useState<string>("");
  const [hasData, setHasData] = useState(false);

  useEffect(() => {
    function handleMessage(event: MessageEvent) {
      const message = event.data as { type?: string; payload?: { renderData?: RenderData } };
      if (message?.type === "ui-lifecycle-iframe-render-data") {
        setHasData(true);
        const renderData = message.payload?.renderData;
        const nextIdentifier = renderData?.toolInput?.identifier || "";
        const rawText = extractText(renderData?.toolOutput);
        const cleaned = stripFrontmatter(rawText).trim();
        const parsedTitle = parseTitle(cleaned);

        setIdentifier(nextIdentifier);
        setTitle(parsedTitle || nextIdentifier || "Note Preview");
        setContent(cleaned);
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

  return (
    <div className="min-h-screen bg-background text-foreground" data-theme="light">
      <div className="mx-auto flex max-w-4xl flex-col gap-4 px-6 py-6">
        <div className="flex items-center justify-between gap-4">
          <div>
            <h1 className="text-lg font-semibold">{title}</h1>
            <p className="text-muted-foreground text-xs">
              {identifier ? `Identifier: ${identifier}` : "Note content"}
            </p>
          </div>
          <Badge variant="secondary">tool-ui (React)</Badge>
        </div>

        <div className="border-border bg-card rounded-2xl border p-4 shadow-sm">
          {content ? (
            <pre className="text-xs leading-relaxed whitespace-pre-wrap font-mono text-foreground/80">
              {content}
            </pre>
          ) : (
            <div className="text-muted-foreground text-sm">No content available.</div>
          )}
        </div>
      </div>
    </div>
  );
}

const root = document.getElementById("root");
if (root) {
  createRoot(root).render(<NotePreviewApp />);
}
