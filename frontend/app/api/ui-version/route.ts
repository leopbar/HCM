import { readdir, stat } from "node:fs/promises";
import path from "node:path";

export const dynamic = "force-dynamic";

/** Moment the screens' source files last changed; open tabs compare it with when they were loaded. */
export async function GET() {
  const root = path.join(process.cwd(), "app");
  let latest = 0;
  for (const entry of await readdir(root, { recursive: true, withFileTypes: true })) {
    if (entry.isFile() && /\.(tsx?|css)$/.test(entry.name)) {
      latest = Math.max(latest, (await stat(path.join(entry.parentPath, entry.name))).mtimeMs);
    }
  }
  return Response.json({ changed_at: latest }, { headers: { "Cache-Control": "no-store" } });
}
