/** Read-only Pi tools for host-validated Journl Knowledge folders. */
import { constants, readFileSync } from "node:fs";
import { lstat, open, readdir, realpath } from "node:fs/promises";
import { isAbsolute, join, normalize, relative, resolve, sep } from "node:path";
import { Type } from "@earendil-works/pi-ai";
import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";

type Reference = { tag: string; kind: string; description: string; path: string };
const manifestPath = process.env.JOURNL_REFERENCE_MANIFEST || "";
let references: Reference[] = [];
if (manifestPath) {
  const parsed = JSON.parse(readFileSync(manifestPath, "utf8"));
  if (!Array.isArray(parsed)) throw new Error("Journl reference manifest must be an array");
  references = parsed.filter((item): item is Reference =>
    item && typeof item.tag === "string" && typeof item.path === "string" && isAbsolute(item.path));
}

const textExtensions = new Set([".md", ".txt", ".json", ".jsonl", ".yaml", ".yml", ".toml", ".csv", ".tsv", ".log"]);

function result(text: string, details: Record<string, unknown> = {}) {
  return { content: [{ type: "text" as const, text }], details };
}

function selected(tag?: string): Reference[] {
  if (!tag) return references;
  const clean = tag.replace(/^#/, "").toLocaleLowerCase("de-DE");
  return references.filter((item) => item.tag.toLocaleLowerCase("de-DE") === clean);
}

async function safePath(root: string, requested = "", requireFile = false): Promise<string> {
  if (requested.includes("\0") || isAbsolute(requested)) throw new Error("Only relative reference paths are allowed");
  const clean = normalize(requested || ".");
  if (clean === ".." || clean.startsWith(`..${sep}`)) throw new Error("Reference path escapes its allowed folder");
  const candidate = resolve(root, clean);
  const rel = relative(root, candidate);
  if (rel === ".." || rel.startsWith(`..${sep}`) || isAbsolute(rel)) throw new Error("Reference path escapes its allowed folder");
  let current = root;
  for (const part of rel.split(sep).filter(Boolean)) {
    current = join(current, part);
    if ((await lstat(current)).isSymbolicLink()) throw new Error("Symbolic links are not allowed in Knowledge references");
  }
  const resolved = await realpath(candidate);
  const resolvedRel = relative(root, resolved);
  if (resolvedRel === ".." || resolvedRel.startsWith(`..${sep}`) || isAbsolute(resolvedRel)) throw new Error("Resolved path escapes its allowed folder");
  const stat = await lstat(resolved);
  if (requireFile && !stat.isFile()) throw new Error("Reference path is not a regular file");
  return resolved;
}

async function walk(root: string, start = "", maxFiles = 2000): Promise<string[]> {
  const base = await safePath(root, start);
  const output: string[] = [];
  const queue = [base];
  while (queue.length && output.length < maxFiles) {
    const directory = queue.shift()!;
    for (const entry of await readdir(directory, { withFileTypes: true })) {
      if (entry.isSymbolicLink()) continue;
      const path = join(directory, entry.name);
      if (entry.isDirectory()) queue.push(path);
      else if (entry.isFile()) output.push(path);
      if (output.length >= maxFiles) break;
    }
  }
  return output;
}

async function safeText(root: string, requested: string, maxBytes: number): Promise<string> {
  const path = await safePath(root, requested, true);
  const handle = await open(path, constants.O_RDONLY | constants.O_NOFOLLOW);
  try {
    const stat = await handle.stat();
    if (!stat.isFile() || stat.size > maxBytes) throw new Error("Datei ist zu groß oder keine reguläre Datei");
    const content = await handle.readFile("utf8");
    if (content.includes("\0")) throw new Error("Binärdateien sind nicht erlaubt");
    return content;
  } finally {
    await handle.close();
  }
}

function extension(path: string): string {
  const name = path.toLocaleLowerCase("de-DE");
  const dot = name.lastIndexOf(".");
  return dot < 0 ? "" : name.slice(dot);
}

export default function (pi: ExtensionAPI) {
  pi.registerTool({
    name: "journl_reference_list",
    label: "Journl Knowledge auflisten",
    description: "Listet freigegebene externe Knowledge-Ordner oder deren Dateien rekursiv auf. Nur Lesen, keine Änderungen.",
    parameters: Type.Object({
      tag: Type.Optional(Type.String({ description: "Knowledge-Hashtag mit oder ohne #" })),
      path: Type.Optional(Type.String({ description: "Relativer Unterordner" })),
      limit: Type.Optional(Type.Number({ minimum: 1, maximum: 200 })),
    }),
    async execute(_id, params) {
      const limit = Math.max(1, Math.min(200, Math.floor(params.limit || 100)));
      const items: string[] = [];
      for (const ref of selected(params.tag)) {
        for (const file of await walk(ref.path, params.path || "", limit - items.length)) {
          items.push(`#${ref.tag}:${relative(ref.path, file).split(sep).join("/")}`);
          if (items.length >= limit) break;
        }
      }
      return result(items.length ? items.join("\n") : "Keine passenden freigegebenen Dateien.", { count: items.length });
    },
  });

  pi.registerTool({
    name: "journl_reference_search",
    label: "Journl Knowledge durchsuchen",
    description: "Sucht eine Zeichenfolge, insbesondere Hashtags, rekursiv in freigegebenen externen Textdateien. Treffer und Ausgaben sind begrenzt.",
    parameters: Type.Object({
      query: Type.String({ minLength: 1, maxLength: 200, description: "Wörtlich gesuchter Text, z. B. #mein-tag" }),
      tag: Type.Optional(Type.String({ description: "Knowledge-Hashtag mit oder ohne #" })),
      limit: Type.Optional(Type.Number({ minimum: 1, maximum: 100 })),
    }),
    async execute(_id, params) {
      const limit = Math.max(1, Math.min(100, Math.floor(params.limit || 50)));
      const query = params.query.toLocaleLowerCase("de-DE");
      const hits: string[] = [];
      for (const ref of selected(params.tag)) {
        for (const file of await walk(ref.path)) {
          if (!textExtensions.has(extension(file))) continue;
          let content: string;
          try { content = await safeText(ref.path, relative(ref.path, file), 512_000); } catch { continue; }
          const lines = content.split(/\r?\n/);
          for (let index = 0; index < lines.length; index += 1) {
            if (lines[index].toLocaleLowerCase("de-DE").includes(query)) {
              const rel = relative(ref.path, file).split(sep).join("/");
              hits.push(`#${ref.tag}:${rel}:${index + 1}: ${lines[index].slice(0, 500)}`);
              if (hits.length >= limit) return result(hits.join("\n"), { count: hits.length, truncated: true });
            }
          }
        }
      }
      return result(hits.length ? hits.join("\n") : "Keine Treffer.", { count: hits.length, truncated: false });
    },
  });

  pi.registerTool({
    name: "journl_reference_read",
    label: "Journl Knowledge lesen",
    description: "Liest gezielt einen relativen UTF-8-Textdateipfad aus genau einer freigegebenen Knowledge-Quelle. Maximal 200 KB.",
    parameters: Type.Object({
      tag: Type.String({ description: "Knowledge-Hashtag mit oder ohne #" }),
      path: Type.String({ minLength: 1, maxLength: 500, description: "Relativer Dateipfad aus journl_reference_list/search" }),
      start_line: Type.Optional(Type.Number({ minimum: 1 })),
      line_count: Type.Optional(Type.Number({ minimum: 1, maximum: 500 })),
    }),
    async execute(_id, params) {
      const refs = selected(params.tag);
      if (refs.length !== 1) throw new Error("Knowledge-Hashtag ist nicht eindeutig freigegeben");
      const path = await safePath(refs[0].path, params.path, true);
      if (!textExtensions.has(extension(path))) throw new Error("Kein unterstützter Texttyp");
      const content = await safeText(refs[0].path, params.path, 200_000);
      const lines = content.split(/\r?\n/);
      const start = Math.max(1, Math.floor(params.start_line || 1));
      const count = Math.max(1, Math.min(500, Math.floor(params.line_count || 200)));
      const end = Math.min(lines.length, start - 1 + count);
      const shown = lines.slice(start - 1, end).map((line, index) => `${start + index}: ${line}`).join("\n");
      return result(shown || "(leer)", { tag: refs[0].tag, path: params.path, start, end, total_lines: lines.length });
    },
  });
}
