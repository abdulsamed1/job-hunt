import { describe, expect, it } from "vitest";
import {
  fetchPersonio,
  fetchRecruitee,
  fetchTeamtailor,
  personioFeedUrl,
  recruiteeApiUrl,
  teamtailorFeedUrl,
} from "../src/adapters/tenants.js";

// Slice 5 tenant boards: URL derivation mirrors the Python adapters, and live
// yield was proven per tenant (see task-5 report). These tests use stubbed
// fetch with live-shaped payloads — no network in tests.

const TT_RSS = `<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:tt="http://www.teamtailor.com/ns">
<channel><title>Jobs</title>
<item><title>Backend Engineer</title><link>https://career.teamtailor.com/jobs/123-backend-engineer</link>
<guid>https://career.teamtailor.com/jobs/123-backend-engineer</guid>
<pubDate>Mon, 06 Oct 2026 09:00:00 GMT</pubDate>
<description><![CDATA[<p>Build APIs.</p>]]></description>
<remoteStatus>Hybrid</remoteStatus>
<tt:locations><tt:location><tt:city>Berlin</tt:city><tt:country>Germany</tt:country></tt:location></tt:locations>
</item>
<item><title>Remote Designer</title><link>https://career.teamtailor.com/jobs/124-remote-designer</link>
<guid>x</guid><pubDate>Tue, 07 Oct 2026 09:00:00 GMT</pubDate>
<description>Design things.</description>
<remoteStatus>Fully remote</remoteStatus>
<tt:locations><tt:location><tt:city></tt:city><tt:country></tt:country></tt:location></tt:locations>
</item>
</channel></rss>`;

const RECRUITEE_JSON = {
  offers: [
    {
      id: 42, title: "Delivery Lead", slug: "delivery-lead",
      careers_url: "https://make.recruitee.com/o/delivery-lead-3",
      description: "<p>Lead delivery.</p>", requirements: "<ul><li>Python</li></ul>",
      remote: true, city: "", country: "", locations: [], department: "Ops",
      employment_type_code: "full_time", published_at: "2026-10-06T00:00:00Z",
      company_name: "Make",
    },
    { id: 43, title: "", careers_url: "https://make.recruitee.com/o/untitled" },
  ],
};

const PERSONIO_XML = `<workzag-jobs>
<position><id>2452785</id><name>AML Process Owner</name><subcompany>VividTech Limited</subcompany>
<office>Limassol</office><department>Compliance</department><employmentType>full-time</employmentType>
<schedule>Full-time</schedule><createdAt>2026-10-01T00:00:00.000Z</createdAt>
<jobDescriptions><jobDescription><name>Your profile</name><value><![CDATA[<p>Know <b>AML</b>.</p>]]></value></jobDescription></jobDescriptions>
</position>
<position><id></id><name>No ID here</name><office>Nowhere</office></position>
</workzag-jobs>`;

function stubFetch(handler: (url: string) => unknown) {
  const prev = globalThis.fetch;
  (globalThis as any).fetch = async (url: string) => {
    const body = handler(String(url));
    if (body === null) return new Response("nope", { status: 404 });
    const text = typeof body === "string" ? body : JSON.stringify(body);
    return new Response(text, { status: 200, headers: { "Content-Type": "application/json" } });
  };
  return () => {
    globalThis.fetch = prev;
  };
}

describe("tenant feed URL derivation", () => {
  it("teamtailor appends jobs.rss, passes .rss through", () => {
    expect(teamtailorFeedUrl("https://career.teamtailor.com")).toBe("https://career.teamtailor.com/jobs.rss");
    expect(teamtailorFeedUrl("https://softwarefinder.na.teamtailor.com")).toBe(
      "https://softwarefinder.na.teamtailor.com/jobs.rss",
    );
    expect(teamtailorFeedUrl("https://x.teamtailor.com/jobs.rss")).toBe("https://x.teamtailor.com/jobs.rss");
    expect(teamtailorFeedUrl("")).toBe("");
  });

  it("recruitee derives the tenant API URL from the board host", () => {
    expect(recruiteeApiUrl("https://make.recruitee.com")).toBe("https://make.recruitee.com/api/offers/");
    expect(recruiteeApiUrl("https://happeo.recruitee.com/careers")).toBe("https://happeo.recruitee.com/api/offers/");
    expect(recruiteeApiUrl("https://example.com")).toBe("");
  });

  it("personio appends /xml, passes /xml through", () => {
    expect(personioFeedUrl("https://vivid.jobs.personio.de")).toBe("https://vivid.jobs.personio.de/xml");
    expect(personioFeedUrl("https://vivid.jobs.personio.de/xml")).toBe("https://vivid.jobs.personio.de/xml");
    expect(personioFeedUrl("")).toBe("");
  });
});

describe("tenant fetchers", () => {
  it("fetchTeamtailor parses items with tt: locations and remote status", async () => {
    const restore = stubFetch(() => TT_RSS);
    try {
      const out = await fetchTeamtailor("https://career.teamtailor.com", "teamtailor-career");
      expect(out).toHaveLength(2);
      expect(out[0].title).toBe("Backend Engineer");
      expect(out[0].location).toBe("Berlin, Germany");
      expect(out[1].location).toBe("Remote");
      for (const j of out) expect(j.title && j.company && j.url && j.desc).toBeTruthy();
    } finally {
      restore();
    }
  });

  it("fetchTeamtailor returns [] on non-200", async () => {
    const restore = stubFetch(() => null);
    try {
      expect(await fetchTeamtailor("https://career.teamtailor.com", "teamtailor-career")).toEqual([]);
    } finally {
      restore();
    }
  });

  it("fetchRecruitee parses offers, skips title-less rows", async () => {
    const restore = stubFetch(() => RECRUITEE_JSON);
    try {
      const out = await fetchRecruitee("https://make.recruitee.com", "recruitee-make");
      expect(out).toHaveLength(1);
      expect(out[0].title).toBe("Delivery Lead");
      expect(out[0].company).toBe("Make");
      expect(out[0].location).toBe("Remote");
      expect(out[0].desc).toContain("Lead delivery.");
      expect(out[0].external_id).toBe("42");
    } finally {
      restore();
    }
  });

  it("fetchRecruitee returns [] on wrong-shape JSON", async () => {
    const restore = stubFetch(() => ({ jobs: [] }));
    try {
      expect(await fetchRecruitee("https://make.recruitee.com", "recruitee-make")).toEqual([]);
    } finally {
      restore();
    }
  });

  it("fetchPersonio parses positions with per-job URLs, skips id-less rows", async () => {
    const restore = stubFetch(() => PERSONIO_XML);
    try {
      const out = await fetchPersonio("https://vivid.jobs.personio.de", "personio-vivid");
      expect(out).toHaveLength(1);
      expect(out[0].title).toBe("AML Process Owner");
      expect(out[0].company).toBe("VividTech Limited");
      expect(out[0].location).toBe("Limassol");
      expect(out[0].url).toBe("https://vivid.jobs.personio.de/job/2452785");
      expect(out[0].desc).toContain("AML");
      expect(out[0].external_id).toBe("2452785");
    } finally {
      restore();
    }
  });

  it("fetchPersonio returns [] when no positions exist", async () => {
    const restore = stubFetch(() => `<workzag-jobs></workzag-jobs>`);
    try {
      expect(await fetchPersonio("https://finn.jobs.personio.de", "personio-finn")).toEqual([]);
    } finally {
      restore();
    }
  });
});
