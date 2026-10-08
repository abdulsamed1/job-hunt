import { describe, it, expect, vi } from "vitest";
// @ts-ignore: node types not installed in worker; vitest provides fs at runtime
import { readFileSync } from "fs";
import { routeChatCompletion, refuseSensitive, salvageRootJson } from "../src/lib/llm.js";

const ok = (text: string) => async () => new Response(
  JSON.stringify({ choices: [{ message: { content: text } }], usage: { prompt_tokens: 10, completion_tokens: 5 } }),
  { status: 200, headers: { "content-type": "application/json" } },
);

describe("routeChatCompletion", () => {
  it("falls through on 429 to the next provider", async () => {
    const calls: string[] = [];
    const fetch429 = async () => new Response("slow", { status: 429 });
    const env: any = {
      AI: null,
      LLM_PROVIDERS: "p1,p2",
      P1_URL: "https://p1.example/v1", P1_KEY: "k1", P1_MODEL: "m1",
      P2_URL: "https://p2.example/v1", P2_KEY: "k2", P2_MODEL: "m2",
      _fetch: async (url: string, init: any) => {
        calls.push(url);
        return url.includes("p1") ? fetch429() : ok('{"a":1}')();
      },
    };
    const db: any = { prepare: () => ({ bind: (...a: any[]) => ({ run: async () => ({}), first: async () => null }) }) };
    const r = await routeChatCompletion(env, db, { messages: [{ role: "user", content: "hi" }], maxTokens: 200, purpose: "test" });
    expect(r.provider).toBe("p2");
    expect(calls.length).toBe(2);
  });
});

describe("output discipline", () => {
  it("refuses sensitive fields", () => {
    expect(refuseSensitive("Are you authorized to work in the US?")).toBe(true);
    expect(refuseSensitive("Desired salary")).toBe(false);
    expect(refuseSensitive("Current salary")).toBe(true);
    expect(refuseSensitive("Salary history")).toBe(true);
    expect(refuseSensitive("What are your salary expectations?")).toBe(false);
  });
  it("salvages root-level JSON only", () => {
    const r = salvageRootJson('{"a": {"value": "x", "needs_confirmation": true}, "b":');
    expect(r.obj.a.needs_confirmation).toBe(true);
    expect(r.truncated).toBe(true);
  });
});

describe("advisory boundary", () => {
  it("eligibility flows only from the deterministic score", () => {
    const index = readFileSync("src/index.ts", "utf8");
    // The only saveEvaluation call must pass the deterministic result's fields.
    expect(index).toMatch(/saveEvaluation\(db, m\.hash, r\.score, r\.eligible/);
    // No LLM output may feed eligibility.
    expect(index).not.toMatch(/eligible[^\n]*completeJson|completeJson[^\n]*eligible/);
    // Alias-bypass hardening: eligibility must never flow from LLM output under another name.
    expect(index).not.toMatch(/out\.obj\.eligible|out\.eligible/);
    // The apply queue gate still runs on the deterministic result.
    expect(index).toMatch(/if \(r\.eligible\)/);
  });
});

describe("hardening: budgets, mapping, retry", () => {
  const memDb = () => {
    const rows = new Map<string, any>();
    const writes: any[][] = [];
    return {
      writes,
      db: {
        prepare: (sql: string) => ({
          bind: (...a: any[]) => ({
            run: async () => { writes.push([sql, a]); return {}; },
            first: async () => {
              if (sql.includes("SUM(tokens)")) {
                // Window-aware like the real table: only the bound window counts.
                const w = a[0];
                let t = 0;
                for (const [k, v] of rows) if (k === `tok:${w}`) t += v;
                return { t };
              }
              if (sql.includes("disabled_until")) {
                const key = `dis:${a[0]}:${a[1]}`;
                return rows.has(key) ? { disabled_until: rows.get(key) } : null;
              }
              return null;
            },
          }),
        }),
      },
      seedTokens: (n: number, w = "2099-01") => rows.set(`tok:${w}`, n),
    };
  };

  it("pins default provider order without sambanova", async () => {
    const { routeChatCompletion } = await import("../src/lib/llm.js");
    const { db } = memDb();
    const env: any = {
      AI: null, LLM_MAX_PROVIDERS_PER_MESSAGE: "9",
      GROQ_URL: "https://g.example/v1", GROQ_KEY: "k", GROQ_MODEL: "m",
      CEREBRAS_URL: "https://c.example/v1", CEREBRAS_KEY: "k", CEREBRAS_MODEL: "m",
      MISTRAL_URL: "https://m.example/v1", MISTRAL_KEY: "k", MISTRAL_MODEL: "m",
      OPENAI_COMPAT_BASE: "https://o.example/v1", OPENAI_COMPAT_KEY: "k", OPENAI_COMPAT_MODEL: "m",
      _fetch: async () => new Response("no", { status: 500 }),
    };
    const err: Error = await routeChatCompletion(env, db, { messages: [{ role: "user", content: "hi" }], maxTokens: 10, purpose: "t" }).then(
      () => { throw new Error("should have thrown"); },
      (e) => e,
    );
    expect(err.message).toMatch(/groq.*cerebras.*mistral.*openai-compat/s);
    expect(err.message).not.toMatch(/sambanova/);
  });

  it("stops after LLM_MAX_PROVIDERS_PER_MESSAGE", async () => {
    const { routeChatCompletion } = await import("../src/lib/llm.js");
    const { db } = memDb();
    let calls = 0;
    const env: any = {
      AI: null, LLM_PROVIDERS: "p1,p2,p3", LLM_MAX_PROVIDERS_PER_MESSAGE: "1",
      P1_URL: "https://1.example/v1", P1_KEY: "k", P1_MODEL: "m",
      P2_URL: "https://2.example/v1", P2_KEY: "k", P2_MODEL: "m",
      P3_URL: "https://3.example/v1", P3_KEY: "k", P3_MODEL: "m",
      _fetch: async () => { calls++; return new Response("no", { status: 500 }); },
    };
    await expect(routeChatCompletion(env, db, { messages: [{ role: "user", content: "hi" }], maxTokens: 10, purpose: "t" })).rejects.toThrow(/stopped after 1 providers/);
    expect(calls).toBe(1);
  });

  it("refuses without fetching when the monthly budget is exhausted", async () => {
    const { routeChatCompletion, currentWindow } = await import("../src/lib/llm.js");
    const { db, seedTokens } = memDb();
    seedTokens(2000000, currentWindow());
    let calls = 0;
    const env: any = {
      AI: null, LLM_MONTHLY_TOKEN_BUDGET: "100",
      GROQ_URL: "https://g.example/v1", GROQ_KEY: "k", GROQ_MODEL: "m",
      _fetch: async () => { calls++; return new Response("no", { status: 500 }); },
    };
    await expect(routeChatCompletion(env, db, { messages: [{ role: "user", content: "hi" }], maxTokens: 10, purpose: "t" }))
      .rejects.toThrow(/budget exhausted/);
    expect(calls).toBe(0);
  });

  it("scopes usage and disables to the monthly window", async () => {
    const { routeChatCompletion, currentWindow } = await import("../src/lib/llm.js");
    const store = memDb();
    const env: any = {
      AI: null, LLM_PROVIDERS: "p1",
      P1_URL: "https://1.example/v1", P1_KEY: "k", P1_MODEL: "m",
      _fetch: async () => new Response("slow", { status: 429 }),
    };
    await expect(routeChatCompletion(env, store.db, { messages: [{ role: "user", content: "hi" }], maxTokens: 10, purpose: "t" })).rejects.toThrow();
    const dis = store.writes.find((w) => String(w[0]).includes("disabled_until"));
    expect(dis).toBeTruthy();
    expect((dis as any[])[1][1]).toBe(currentWindow());
  });

  it("writes a 10-minute disable on 429", async () => {
    const { routeChatCompletion } = await import("../src/lib/llm.js");
    const store = memDb();
    const env: any = {
      AI: null, LLM_PROVIDERS: "p1",
      P1_URL: "https://1.example/v1", P1_KEY: "k", P1_MODEL: "m",
      _fetch: async () => new Response("slow", { status: 429 }),
    };
    await expect(routeChatCompletion(env, store.db, { messages: [{ role: "user", content: "hi" }], maxTokens: 10, purpose: "t" })).rejects.toThrow(/p1: 429/);
    const dis = store.writes.find((w) => String(w[0]).includes("disabled_until"));
    expect(dis).toBeTruthy();
    const until = (dis as any[])[1][2];
    expect(until - Date.now()).toBeGreaterThan(9 * 60 * 1000);
    expect(until - Date.now()).toBeLessThanOrEqual(10 * 60 * 1000 + 5000);
  });

  it("completeJson retries once on garbage then succeeds truncated", async () => {
    const { completeJson } = await import("../src/lib/llm.js");
    const { db } = memDb();
    let n = 0;
    const env: any = {
      AI: null, LLM_PROVIDERS: "p1",
      P1_URL: "https://1.example/v1", P1_KEY: "k", P1_MODEL: "m",
      _fetch: async () => {
        n++;
        const text = n === 1 ? "not json at all" : '{"rationale":"fits well"}';
        return new Response(JSON.stringify({ choices: [{ message: { content: text } }], usage: { prompt_tokens: 5, completion_tokens: 5 } }), { status: 200, headers: { "content-type": "application/json" } });
      },
    };
    const r = await completeJson(env, db, { messages: [{ role: "user", content: "hi" }], maxTokens: 50, purpose: "t" });
    expect(r.obj.rationale).toBe("fits well");
    expect(n).toBe(2);
  });
});
