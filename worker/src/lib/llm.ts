export interface LlmRequest { messages: { role: string; content: string }[]; maxTokens: number; purpose: string; }
export interface LlmResult { text: string; provider: string; model: string; usage: { prompt: number; completion: number }; truncated: boolean; }

interface ProviderDef { name: string; url: string; key: string; model: string; }

function providersFromEnv(env: any): ProviderDef[] {
  const out: ProviderDef[] = [];
  if (env.AI) out.push({ name: "workers-ai", url: "", key: "", model: env.WORKERS_AI_MODEL || "@cf/meta/llama-3.1-8b-instruct" });
  for (const p of String(env.LLM_PROVIDERS || "groq,cerebras,mistral,openai-compat").split(",")) {
    const n = p.trim().toUpperCase().replace(/-/g, "_");
    const url = env[`${n}_URL`] || env.OPENAI_COMPAT_BASE;
    const key = env[`${n}_KEY`] || env.OPENAI_COMPAT_KEY;
    const model = env[`${n}_MODEL`] || "auto";
    if (url && key) out.push({ name: p.trim(), url, key, model });
  }
  return out;
}

export async function providerDisabledUntil(db: any, provider: string): Promise<number> {
  const row: any = await db.prepare(`SELECT disabled_until FROM llm_usage WHERE provider = ? AND window = 'cur'`).bind(provider).first().catch(() => null);
  return row?.disabled_until ?? 0;
}

export async function routeChatCompletion(env: any, db: any, req: LlmRequest): Promise<LlmResult> {
  const fetcher = env._fetch || fetch;
  const errors: string[] = [];
  for (const p of providersFromEnv(env)) {
    if (Date.now() < await providerDisabledUntil(db, p.name)) continue;
    try {
      let text: string; let usage = { prompt: 0, completion: 0 };
      if (p.name === "workers-ai") {
        const res: any = await env.AI.run(p.model, { messages: req.messages, max_tokens: req.maxTokens });
        text = res?.response || "";
      } else {
        const ctl = new AbortController();
        const t = setTimeout(() => ctl.abort(), 25_000);
        let res: Response;
        try {
          res = await fetcher(`${p.url.replace(/\/$/, "")}/chat/completions`, {
            method: "POST", signal: ctl.signal,
            headers: { "content-type": "application/json", authorization: `Bearer ${p.key}` },
            body: JSON.stringify({ model: p.model, messages: req.messages, max_tokens: req.maxTokens }),
          });
        } finally { clearTimeout(t); }
        if (res.status === 401 || res.status === 403) {
          await db.prepare(`INSERT INTO llm_usage (provider, window, requests, tokens, disabled_until) VALUES (?, 'cur', 0, 0, ?) ON CONFLICT(provider, window) DO UPDATE SET disabled_until=excluded.disabled_until`).bind(p.name, Date.now() + 3600_000).run().catch(() => {});
          errors.push(`${p.name}: auth ${res.status}, disabled 1h`);
          continue;
        }
        if (res.status === 429 || res.status >= 500) { errors.push(`${p.name}: ${res.status}`); continue; }
        if (!res.ok) { errors.push(`${p.name}: ${res.status}`); continue; }
        const data: any = await res.json();
        text = data?.choices?.[0]?.message?.content || "";
        usage = { prompt: data?.usage?.prompt_tokens ?? 0, completion: data?.usage?.completion_tokens ?? 0 };
      }
      await recordUsage(db, p.name, usage.prompt + usage.completion).catch(() => {});
      return { text, provider: p.name, model: p.model, usage, truncated: false };
    } catch (e) {
      errors.push(`${p.name}: ${String(e).slice(0, 80)}`);
    }
  }
  throw new Error(`all LLM providers failed: ${errors.join("; ").slice(0, 300)}`);
}

export async function recordUsage(db: any, provider: string, tokens: number): Promise<void> {
  await db.prepare(`INSERT INTO llm_usage (provider, window, requests, tokens, disabled_until) VALUES (?, 'cur', 1, ?, 0) ON CONFLICT(provider, window) DO UPDATE SET requests=requests+1, tokens=tokens+excluded.tokens`).bind(provider, tokens).run();
}
