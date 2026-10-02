import { describe, it, expect, vi } from "vitest";
import { routeChatCompletion } from "../src/lib/llm.js";

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
