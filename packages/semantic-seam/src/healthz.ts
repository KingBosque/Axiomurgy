/** GET /healthz — mirrors scripts/eval_semantic_recommendations.fetch_healthz */

export async function fetchHealthz(
  baseUrl: string,
  options: { timeoutS?: number } = {},
): Promise<{ status_code: number; body: unknown } | null> {
  const timeoutS = options.timeoutS ?? 5;
  const url = `${baseUrl.replace(/\/+$/, "")}/healthz`;
  const ac = new AbortController();
  const t = setTimeout(() => ac.abort(), Math.max(100, timeoutS * 1000));
  try {
    const r = await fetch(url, { method: "GET", signal: ac.signal });
    const text = await r.text();
    let body: unknown;
    try {
      body = text ? JSON.parse(text) : {};
    } catch {
      body = { text: text.slice(0, 500) };
    }
    return { status_code: r.status, body };
  } catch {
    return null;
  } finally {
    clearTimeout(t);
  }
}
