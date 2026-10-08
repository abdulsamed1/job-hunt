export interface LlmRequest { messages: { role: string; content: string }[]; maxTokens: number; purpose: string; }
export interface LlmResult { text: string; provider: string; model: string; usage: { prompt: number; completion: number }; truncated: boolean; }

interface ProviderDef { name: string; url: string; key: string; model: string; }

/** Monthly budget window. The old 'cur' window never rotated, so budgets
 * could never be enforced — YYYY-MM rotates automatically. */
export function currentWindow(d = new Date()): string {
  return `${d.getUTCFullYear()}-${String(d.getUTCMonth() + 1).padStart(2, "0")}`;
}

function numEnv(env: any, name: string, fallback: number): number {
  const v = parseFloat(env[name]);
  return Number.isFinite(v) && v > 0 ? v : fallback;
}

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

export async function providerDisabledUntil(db: any, provider: string, window?: string): Promise<number> {
  const w = window || currentWindow();
  const row: any = await db.prepare(`SELECT disabled_until FROM llm_usage WHERE provider = ? AND window = ?`).bind(provider, w).first().catch(() => null);
  return row?.disabled_until ?? 0;
}

async function disableProvider(db: any, provider: string, ms: number, reason: string, errors: string[]): Promise<void> {
  const w = currentWindow();
  await db.prepare(`INSERT INTO llm_usage (provider, window, requests, tokens, disabled_until) VALUES (?, ?, 0, 0, ?) ON CONFLICT(provider, window) DO UPDATE SET disabled_until=excluded.disabled_until`).bind(provider, w, Date.now() + ms).run().catch(() => {});
  errors.push(`${provider}: ${reason}, disabled ${Math.round(ms / 60000)}m`);
}

/** Enforced monthly token budget. Throws (fail safe to deterministic scoring)
 * when the current window is exhausted. Workers-AI usage records 0 tokens,
 * so metered providers are what this guards. */
export async function checkTokenBudget(db: any, env: any): Promise<void> {
  const budget = numEnv(env, "LLM_MONTHLY_TOKEN_BUDGET", 1000000);
  const row: any = await db.prepare(`SELECT COALESCE(SUM(tokens), 0) AS t FROM llm_usage WHERE window = ?`).bind(currentWindow()).first().catch(() => ({ t: 0 }));
  const used = row?.t ?? 0;
  if (used >= budget) throw new Error(`LLM monthly token budget exhausted (${used}/${budget})`);
}

export async function routeChatCompletion(env: any, db: any, req: LlmRequest): Promise<LlmResult> {
  await checkTokenBudget(db, env);
  const maxProviders = Math.max(1, Math.floor(numEnv(env, "LLM_MAX_PROVIDERS_PER_MESSAGE", 2)));
  const timeoutMs = numEnv(env, "LLM_TIMEOUT_MS", 8000);
  const fetcher = env._fetch || fetch;
  const errors: string[] = [];
  let tried = 0;
  for (const p of providersFromEnv(env)) {
    if (tried >= maxProviders) {
      errors.push(`stopped after ${maxProviders} providers (LLM_MAX_PROVIDERS_PER_MESSAGE)`);
      break;
    }
    if (Date.now() < await providerDisabledUntil(db, p.name)) continue;
    tried++;
    try {
      let text: string; let usage = { prompt: 0, completion: 0 };
      if (p.name === "workers-ai") {
        const res: any = await env.AI.run(p.model, { messages: req.messages, max_tokens: req.maxTokens });
        text = res?.response || "";
      } else {
        const ctl = new AbortController();
        const t = setTimeout(() => ctl.abort(), timeoutMs);
        let res: Response;
        try {
          res = await fetcher(`${p.url.replace(/\/$/, "")}/chat/completions`, {
            method: "POST", signal: ctl.signal,
            headers: { "content-type": "application/json", authorization: `Bearer ${p.key}` },
            body: JSON.stringify({ model: p.model, messages: req.messages, max_tokens: req.maxTokens }),
          });
        } finally { clearTimeout(t); }
        if (res.status === 401 || res.status === 403) {
          await disableProvider(db, p.name, 3600_000, `auth ${res.status}`, errors);
          continue;
        }
        if (res.status === 429 || res.status >= 500) {
          await disableProvider(db, p.name, 600_000, `${res.status}`, errors);
          continue;
        }
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

export async function recordUsage(db: any, provider: string, tokens: number, window?: string): Promise<void> {
  const w = window || currentWindow();
  await db.prepare(`INSERT INTO llm_usage (provider, window, requests, tokens, disabled_until) VALUES (?, ?, 1, ?, 0) ON CONFLICT(provider, window) DO UPDATE SET requests=requests+1, tokens=tokens+excluded.tokens`).bind(provider, w, tokens).run();
}

const SENSITIVE_RX = /authoriz|visa|sponsor|citizen|current salary|salary histor|compensat|pay histor|disab|veteran|gender|race|religion|arrest|convict|background check|perjury|attest|certif|swear/i;

export function refuseSensitive(label: string): boolean {
  return SENSITIVE_RX.test(label || "");
}

export function salvageRootJson(raw: string): { obj: any; truncated: boolean } {
  try { return { obj: JSON.parse(raw), truncated: false }; } catch { /* fall through */ }
  let best: any = null;
  let depth = 0; let inStr = false; let esc = false;
  for (let i = 0; i < raw.length; i++) {
    const c = raw[i];
    if (inStr) { if (esc) esc = false; else if (c === "\\") esc = true; else if (c === '"') inStr = false; continue; }
    if (c === '"') { inStr = true; continue; }
    if (c === "{") depth++;
    if (c === "}" || c === ",") {
      if (depth === 1 && (c === "}" || raw[i] === ",")) {
        try { const cand = JSON.parse(raw.slice(0, i + (c === "}" ? 1 : 0)) + (c === "}" ? "" : "}")); best = cand; } catch { /* keep scanning */ }
      }
    }
    if (c === "}") depth = Math.max(0, depth - 1);
  }
  if (best) return { obj: best, truncated: true };
  throw new Error("unparseable LLM output");
}

export async function completeJson(env: any, db: any, req: LlmRequest): Promise<{ obj: any; truncated: boolean; provider: string }> {
  const r = await routeChatCompletion(env, db, req);
  try {
    const s = salvageRootJson(r.text);
    return { obj: s.obj, truncated: s.truncated || r.truncated, provider: r.provider };
  } catch {
    const retry = await routeChatCompletion(env, db, { ...req, messages: [...req.messages, { role: "user", content: "Your last reply was not valid JSON. Reply with valid JSON only, same schema." }] });
    const s = salvageRootJson(retry.text);
    return { obj: s.obj, truncated: true, provider: retry.provider };
  }
}
