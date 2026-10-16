import { describe, expect, it } from "vitest";

// /health must surface the notification ledger so verify_deploy can fail a
// deploy whose schema predates the ledger migration. `null` = unreadable.
function healthEnv(notificationFirst: () => Promise<any>) {
  return {
    DB: {
      prepare: (sql: string) => ({
        first: sql.includes("notification_events")
          ? notificationFirst
          : async () => ({ n: 7 }),
        bind: () => ({ first: async () => ({ n: 7 }) }),
      }),
    },
    THRESHOLD: "70",
  } as any;
}

describe("/health notification ledger", () => {
  it("reports the ledger count when the table exists", async () => {
    const mod = await import("../src/index.js");
    const res = await mod.default.fetch(
      new Request("https://worker/health"), healthEnv(async () => ({ n: 12 })),
    );
    expect(res.status).toBe(200);
    const body: any = await res.json();
    expect(body.ok).toBe(true);
    expect(body.jobs).toBe(7);
    expect(body.notification_events).toBe(12);
  });

  it("reports null when the table is missing (pre-migration deploy)", async () => {
    const mod = await import("../src/index.js");
    const res = await mod.default.fetch(
      new Request("https://worker/health"),
      healthEnv(async () => { throw new Error('no such table: notification_events'); }),
    );
    expect(res.status).toBe(200);
    const body: any = await res.json();
    expect(body.ok).toBe(true);
    expect(body.notification_events).toBeNull();
  });
});
