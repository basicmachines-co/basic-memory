import { execFileSync } from "node:child_process";
import { mkdir, readFile, writeFile } from "node:fs/promises";
import path from "node:path";
import { fileURLToPath } from "node:url";
import esbuild from "esbuild";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const rootDir = __dirname;
const distDir = path.join(rootDir, "dist");
const htmlDir = path.resolve(rootDir, "..", "..", "src", "basic_memory", "mcp", "ui", "html");

const tailwindBin = path.join(rootDir, "node_modules", ".bin", "tailwindcss");
const cssInput = path.join(rootDir, "src", "styles.css");
const cssOutput = path.join(distDir, "styles.css");

function buildHtml(title, css, js) {
  return `<!doctype html>
<html lang="en">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>${title}</title>
    <style>${css}</style>
  </head>
  <body>
    <div id="root"></div>
    <script>${js}</script>
  </body>
</html>
`;
}

async function buildCss() {
  execFileSync(
    tailwindBin,
    ["-i", cssInput, "-o", cssOutput, "--minify"],
    { stdio: "inherit" },
  );
  return readFile(cssOutput, "utf8");
}

async function buildJs(entry, outfile) {
  await esbuild.build({
    entryPoints: [entry],
    outfile,
    bundle: true,
    minify: true,
    platform: "browser",
    target: ["es2020"],
    jsx: "automatic",
    define: {
      "process.env.NODE_ENV": "\"production\"",
    },
  });
  return readFile(outfile, "utf8");
}

async function run() {
  await mkdir(distDir, { recursive: true });

  const css = await buildCss();

  const searchJsPath = path.join(distDir, "search-results.js");
  const noteJsPath = path.join(distDir, "note-preview.js");

  const [searchJs, noteJs] = await Promise.all([
    buildJs(path.join(rootDir, "src", "search-results.tsx"), searchJsPath),
    buildJs(path.join(rootDir, "src", "note-preview.tsx"), noteJsPath),
  ]);

  await mkdir(htmlDir, { recursive: true });

  const searchHtml = buildHtml("Basic Memory Tool UI Search", css, searchJs);
  const noteHtml = buildHtml("Basic Memory Tool UI Note Preview", css, noteJs);

  await Promise.all([
    writeFile(path.join(htmlDir, "search-results-tool-ui.html"), searchHtml, "utf8"),
    writeFile(path.join(htmlDir, "note-preview-tool-ui.html"), noteHtml, "utf8"),
  ]);
}

run().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
