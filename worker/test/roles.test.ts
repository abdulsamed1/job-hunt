import { describe, expect, it } from "vitest";
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { classifyRole } from "../src/lib/roles.js";

// rejects (verdict "reject")
const REJECTS: Array<[string, string]> = [
  ["Senior Manager, Application Security", "people-management:manager"],
  ["Engineering Team Lead, Platform", "people-management:team lead"],
  ["Head of Engineering", "people-management:head of"],
  ["Director of Software Engineering - Distributed Systems", "people-management:director"],
  ["VP of Engineering", "people-management:vp"],
  ["Deputy Department Manager, PRE Division Product SRE", "people-management:manager"],
  ["Senior Engineering Manager", "people-management:manager"],
  ["Sr Manager, Engineering", "people-management:manager"],
  ["Principal Engineering Manager", "people-management:manager"],
  ["Staff Engineering Manager, Platform", "people-management:manager"],
  ["Account Executive, EMEA", "revenue:account executive"],
  ["Sales Development Representative", "revenue:sales development"],
  ["Marketing Manager", "revenue:marketing"],
  ["Customer Success Manager", "revenue:customer success"],
  ["Technical Recruiter", "non-engineering:recruiter"],
  ["Product Manager", "non-engineering:product"],
  ["Software Engineering Intern", "non-engineering:intern"],
  // "Internship" is the same out-of-scope thing as "Intern".
  ["Software Engineering Internship", "non-engineering:intern"],
  // A seniority marker never makes a revenue title a software role.
  ["Senior Sales Engineer", "revenue:sales"],
  ["Staff Sales Engineer", "revenue:sales"],
  ["Senior Marketing Engineer", "revenue:marketing"],
  ["Senior Customer Success Engineer", "revenue:customer success"],
  // A domain word alone is out of scope: no engineering build role is named.
  ["Senior Accountant", "non-engineering:accountant"],
  ["Finance Analyst", "non-engineering:finance"],
  ["Legal Counsel", "non-engineering:legal"],
  ["Registered Nurse - ICU", "non-engineering:medical"],
];

// accepts (verdict "software")
const ACCEPTS = [
  "Senior Software Engineer, Query",
  "Backend Engineer, Payments",
  "Full Stack Engineer",
  "FullStack Engineer", // "fullstack" is "full stack" written as one word
  "Full-Stack Engineer", // and the hyphenated spelling is the same title
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

// A domain word (accountant, finance, legal, medical) names a field of work,
// not a build role: it never disqualifies a title that names an engineering
// build role. These are software jobs.
const DOMAIN_WORD_BUILD_ROLE = [
  "Full Stack Engineer - Finance",
  "Staff Engineer, Revenue & Finance Automation",
  "Forward Deployed Engineer, Finance [Office of the CTO]",
  "AI Engineer, Legal & Compliance",
  "Senior Data Engineer II, Finance",
  "Finance Systems Engineer, Finance and Strategy",
  "Medical Engineer",
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
    expect(classifyRole("Salesforce Developer, Python")).toEqual({ verdict: "reject", rule: "crm:salesforce" });
    expect(classifyRole("Salesforce Developer, Python", { rejectCrm: false })).toEqual({
      verdict: "software",
      rule: "software:stack",
    });
    // With the CRM gate off the title is no longer rejected; it names the
    // build role ("developer"), so it is a software role rather than unknown.
    expect(classifyRole("Senior Salesforce Developer", { rejectCrm: false })).toEqual({
      verdict: "software",
      rule: "software:build role",
    });
  });
  it("keeps an IC title that matches no reject family, and rejects management titles", () => {
    expect(classifyRole("Lead Software Engineer").verdict).toBe("software");
    expect(classifyRole("Engineering Lead, Software").verdict).toBe("software");
    expect(classifyRole("Staff Engineer, Payments Tools").verdict).toBe("software");
    expect(classifyRole("Lead Engineering Manager").verdict).toBe("reject");
    expect(classifyRole("Team Lead, Backend Engineering").verdict).toBe("reject");
  });
  it("lets a revenue or non-engineering word beat the IC allow", () => {
    // The IC allow stands in for the software allow-list, never for the reject
    // families: a senior sales engineer is a sales role.
    expect(classifyRole("Senior Sales Engineer")).toEqual({ verdict: "reject", rule: "revenue:sales" });
    expect(classifyRole("Senior Marketing Engineer")).toEqual({ verdict: "reject", rule: "revenue:marketing" });
    expect(classifyRole("Senior Engineering Manager")).toEqual({ verdict: "reject", rule: "people-management:manager" });
    expect(classifyRole("Software Engineering Internship")).toEqual({ verdict: "reject", rule: "non-engineering:intern" });
  });
  it("returns unknown for titles with no signal", () => {
    expect(classifyRole("")).toEqual({ verdict: "unknown", rule: "unknown:none" });
    expect(classifyRole("Data Scientist").verdict).toBe("unknown");
    expect(classifyRole("Guru of spreadsheets").verdict).toBe("unknown");
  });
  it("needs more than a single signal word", () => {
    expect(classifyRole("JavaScript")).toEqual({ verdict: "unknown", rule: "unknown:none" }); // no build role
    // A build role is a software signal on its own now: "Sr. Developer" names
    // the build role, so it is a software role even without a technology.
    expect(classifyRole("Sr. Developer").verdict).toBe("software");
    expect(classifyRole("Salesforce")).toEqual({ verdict: "unknown", rule: "unknown:none" }); // no CRM companion
  });
  it("lets a domain word stand only when no build role is named", () => {
    // A domain word names a field of work, not a build role: it never
    // disqualifies an engineering title.
    for (const title of DOMAIN_WORD_BUILD_ROLE) {
      expect(classifyRole(title).verdict, title).toBe("software");
    }
    // Without a build role the domain words still reject.
    expect(classifyRole("Senior Accountant")).toEqual({ verdict: "reject", rule: "non-engineering:accountant" });
    expect(classifyRole("Legal Counsel")).toEqual({ verdict: "reject", rule: "non-engineering:legal" });
    expect(classifyRole("Finance Analyst")).toEqual({ verdict: "reject", rule: "non-engineering:finance" });
    expect(classifyRole("Registered Nurse - ICU")).toEqual({ verdict: "reject", rule: "non-engineering:medical" });
  });
  it("leaves the people-management and revenue families untouched", () => {
    // A build role does not rescue a management or a revenue title.
    expect(classifyRole("Engineering Manager, Data Feeds")).toEqual({ verdict: "reject", rule: "people-management:manager" });
    expect(classifyRole("Sales Engineer")).toEqual({ verdict: "reject", rule: "revenue:sales" });
    expect(classifyRole("Compliance Manager, BSA/AML Program")).toEqual({ verdict: "reject", rule: "people-management:manager" });
  });
  it("matches the shared Python/Worker role-gate fixtures", () => {
    const here = dirname(fileURLToPath(import.meta.url));
    const fixture = join(here, "..", "..", "tests", "fixtures", "role_gate_cases.json");
    const cases = JSON.parse(readFileSync(fixture, "utf8")) as Array<{
      name: string;
      title: string;
      verdict: "software" | "reject";
      rule?: string;
    }>;
    expect(cases.length).toBeGreaterThan(0);
    for (const c of cases) {
      const r = classifyRole(c.title);
      expect(r.verdict, c.name).toBe(c.verdict);
      if (c.rule) expect(r.rule, c.name).toBe(c.rule);
    }
  });
  it("normalizes accented and non-latin titles", () => {
    expect(classifyRole("Ingénieur DevOps")).toEqual({ verdict: "software", rule: "software:devops" });
    expect(classifyRole("مهندس DevOps")).toEqual({ verdict: "software", rule: "software:devops" });
  });
});
