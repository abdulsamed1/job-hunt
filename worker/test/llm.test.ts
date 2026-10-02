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
