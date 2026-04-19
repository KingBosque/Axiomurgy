import { execSync } from "node:child_process";

export function gitHead(repo: string): string | null {
  try {
    const out = execSync("git rev-parse HEAD", { cwd: repo, encoding: "utf8", timeout: 5000 });
    return out.trim();
  } catch {
    return null;
  }
}
