import { describe, expect, it } from "vitest";
import { classifyRole } from "../src/lib/roles.js";

// rejects (verdict "reject")
const REJECTS: Array<[string, string]> = [
  ["Senior Manager, Application Security", "people-management:manager"],
  ["Engineering Team Lead, Platform", "people-management:team lead"],
  ["Head of Engineering", "people-management:head of"],
  ["Director of Software Engineering - Distributed Systems", "people-management:director"],
  ["VP of Engineering", "people-management:vp"],
  ["Deputy Department Manager, PRE Division Product SRE", "people-management:manager"],
  ["Account Executive, EMEA", "revenue:account executive"],
  ["Sales Development Representative", "revenue:sales development"],
  ["Marketing Manager", "revenue:marketing"],
  ["Customer Success Manager", "revenue:customer success"],
  ["Technical Recruiter", "non-engineering:recruiter"],
  ["Product Manager", "non-engineering:product"],
  ["Software Engineering Intern", "non-engineering:intern"],
];

// accepts (verdict "software")
const ACCEPTS = [
  "Senior Software Engineer, Query",
  "Backend Engineer, Payments",
  "Full Stack Engineer",
  "Lead Software Engineer", // IC allow must beat nothing; no reject word here
  "Staff Engineer, Platform",
  "Principal Engineer, Data",
  "Forward Deployed Engineer",
  "Site Reliability Engineer",
  "Android Engineer",
  "Firmware Engineer",
  "QA Automation Engineer",
  "Security Engineer, Product",
  "Node.js Engineer",
  "React Developer",
];

// CRM roles are rejected only while rejectCrm is on (default on)
const CRM = [
  "Senior Salesforce Developer",
  "Salesforce Consultant",
  "Salesforce Architect",
  "Salesforce Administrator",
];

describe("classifyRole", () => {
  it.each(REJECTS)("rejects %s", (title, rule) => {
    expect(classifyRole(title)).toEqual({ verdict: "reject", rule });
  });
  it.each(ACCEPTS)("accepts %s", (title) => {
    expect(classifyRole(title).verdict).toBe("software");
  });
  it.each(CRM)("rejects CRM role %s by default", (title) => {
    expect(classifyRole(title).verdict).toBe("reject");
    expect(classifyRole(title).rule.startsWith("crm:")).toBe(true);
  });
  it("allows CRM roles when rejectCrm is off", () => {
    expect(classifyRole("Senior Salesforce Developer", { rejectCrm: false }).verdict).not.toBe("reject");
  });
  it("prefers the IC allow over a reject word (allow-first ordering)", () => {
    expect(classifyRole("Lead Software Engineer").verdict).toBe("software");
    expect(classifyRole("Engineering Lead, Software").verdict).toBe("software");
    expect(classifyRole("Team Lead, Backend Engineering").verdict).toBe("reject");
  });
  it("returns unknown for titles with no signal", () => {
    expect(classifyRole("")).toEqual({ verdict: "unknown", rule: "unknown:none" });
    expect(classifyRole("Data Scientist").verdict).toBe("unknown");
    expect(classifyRole("Guru of spreadsheets").verdict).toBe("unknown");
  });
});