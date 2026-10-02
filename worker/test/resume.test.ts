import { describe, it, expect } from "vitest";
// @ts-ignore: node types not installed in worker; vitest provides fs at runtime
import { readFileSync } from "fs";

describe("resume bytes policy", () => {
  it("apply path never synthesizes a resume PDF", () => {
    const index = readFileSync("src/index.ts", "utf8");
    expect(index).not.toMatch(/renderPdfBytes/);
  });
});
