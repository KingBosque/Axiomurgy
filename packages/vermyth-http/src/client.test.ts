import { afterEach, describe, expect, it, vi } from "vitest";
import { VermythHttpClient, VermythHttpError, timedCall } from "./client.js";

const originalFetch = globalThis.fetch;

afterEach(() => {
  globalThis.fetch = originalFetch;
});

describe("VermythHttpClient", () => {
  it("posts arcane/recommend and returns JSON body", async () => {
    globalThis.fetch = vi.fn(async (url: URL | RequestInfo) => {
      const u = String(url);
      expect(u).toContain("/arcane/recommend");
      return new Response(JSON.stringify({ recommendations: [{ bundle_id: "b1" }] }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    }) as typeof fetch;

    const c = new VermythHttpClient("http://127.0.0.1:7777/", { timeoutS: 5 });
    const raw = await c.arcaneRecommend({ skillId: "decide", input: { intent: { objective: "x" } } });
    expect(raw).toEqual({ recommendations: [{ bundle_id: "b1" }] });
  });

  it("throws VermythHttpError on HTTP 500", async () => {
    globalThis.fetch = vi.fn(async () => new Response("nope", { status: 500 })) as typeof fetch;

    const c = new VermythHttpClient("http://127.0.0.1:9/");
    await expect(c.decide({ foo: 1 })).rejects.toThrow(VermythHttpError);
    await expect(c.decide({ foo: 1 })).rejects.toThrow(/HTTP 500/);
  });

  it("unwraps tool result for compile_program", async () => {
    globalThis.fetch = vi.fn(async () => {
      return new Response(JSON.stringify({ result: { validation: { ok: true } } }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    }) as typeof fetch;

    const c = new VermythHttpClient("http://127.0.0.1:1/");
    const r = await c.compileProgram({ program_id: "p" });
    expect(r).toEqual({ validation: { ok: true } });
  });

  it("timedCall returns latency", async () => {
    const [v, ms] = await timedCall(async () => 42);
    expect(v).toBe(42);
    expect(ms).toBeGreaterThanOrEqual(0);
  });
});
