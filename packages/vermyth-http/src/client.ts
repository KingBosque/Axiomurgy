/**
 * TypeScript port of axiomurgy/adapters/vermyth_http.VermythHttpClient (JSON-only HTTP seam).
 */

export class VermythHttpError extends Error {
  constructor(message: string, options?: ErrorOptions) {
    super(message, options);
    this.name = "VermythHttpError";
  }
}

function envHttpToken(): string | undefined {
  for (const key of ["AXIOMURGY_VERMYTH_HTTP_TOKEN", "VERMYTH_HTTP_TOKEN"]) {
    const v = process.env[key];
    if (typeof v === "string" && v.trim()) return v.trim();
  }
  return undefined;
}

export type VermythHttpClientOptions = {
  timeoutS?: number;
  /** When omitted, read from AXIOMURGY_VERMYTH_HTTP_TOKEN / VERMYTH_HTTP_TOKEN */
  httpToken?: string | null;
};

export class VermythHttpClient {
  readonly baseUrl: string;
  readonly timeoutS: number;
  private readonly httpToken: string | undefined;

  constructor(baseUrl: string, options: VermythHttpClientOptions = {}) {
    this.baseUrl = baseUrl.replace(/\/+$/, "") + "/";
    this.timeoutS = options.timeoutS ?? 5.0;
    this.httpToken = options.httpToken === null ? undefined : options.httpToken ?? envHttpToken();
  }

  private headers(): Record<string, string> {
    const h: Record<string, string> = {};
    if (this.httpToken) h.Authorization = `Bearer ${this.httpToken}`;
    return h;
  }

  private async postJson(
    path: string,
    body: Record<string, unknown>,
    unwrapToolResult: boolean,
  ): Promise<Record<string, unknown>> {
    const url = new URL(path.replace(/^\/+/, ""), this.baseUrl).toString();
    const ac = new AbortController();
    const t = setTimeout(() => ac.abort(), Math.max(100, this.timeoutS * 1000));
    let r: Response;
    try {
      r = await fetch(url, {
        method: "POST",
        headers: { "Content-Type": "application/json", ...this.headers() },
        body: JSON.stringify(body),
        signal: ac.signal,
      });
    } catch (e) {
      clearTimeout(t);
      throw e;
    }
    clearTimeout(t);
    const text = await r.text();
    if (r.status >= 400) {
      throw new VermythHttpError(`HTTP ${r.status} from ${url}: ${text.slice(0, 500)}`);
    }
    let out: unknown;
    try {
      out = text ? JSON.parse(text) : {};
    } catch (e) {
      throw new VermythHttpError(`invalid JSON from ${url}`, { cause: e });
    }
    if (typeof out !== "object" || out === null || Array.isArray(out)) {
      throw new VermythHttpError("expected JSON object response");
    }
    const o = out as Record<string, unknown>;
    if (unwrapToolResult) {
      const inner = o.result;
      if (typeof inner === "object" && inner !== null && !Array.isArray(inner)) {
        return inner as Record<string, unknown>;
      }
    }
    return o;
  }

  arcaneRecommend(params: {
    skillId: string;
    input: Record<string, unknown>;
    minStrength?: number;
  }): Promise<Record<string, unknown>> {
    const body: Record<string, unknown> = { skill_id: params.skillId, input: params.input };
    if (params.minStrength !== undefined) body.min_strength = params.minStrength;
    return this.postJson("arcane/recommend", body, false);
  }

  compileProgram(program: Record<string, unknown>): Promise<Record<string, unknown>> {
    return this.postJson("tools/compile_program", { program }, true);
  }

  decide(payload: Record<string, unknown>): Promise<Record<string, unknown>> {
    return this.postJson("tools/decide", payload, true);
  }
}

export async function timedCall<T>(fn: () => Promise<T>): Promise<[T, number]> {
  const t0 = performance.now();
  const out = await fn();
  const ms = performance.now() - t0;
  return [out, ms];
}
