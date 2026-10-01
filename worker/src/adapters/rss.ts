// RSS/Atom discovery: cryptojobslist, crypto.jobs, weworkremotely, tokyodev,
// remote3, bitcoinjobs, jobspresso. Mirrors Python FeedAdapter parsing.

import { fetchText, tagText, type RawJob } from "./http.js";

export async function fetchRssFeed(name: string, url: string): Promise<RawJob[]> {
  const text = await fetchText(url);
  if (!text) return [];
  const start = text.search(/<\?xml|<rss|<feed|<rdf/i);
  const xml = start > 0 ? text.slice(start) : text;
  const out: RawJob[] = [];
  for (const chunk of xml.match(/<item[\s\S]*?<\/item>|<entry[\s\S]*?<\/entry>/gi) || []) {
    const title = tagText(chunk, "title");
    const url2 =
      tagText(chunk, "link") || tagText(chunk, "guid") || tagText(chunk, "id");
    if (!title || !url2) continue;
    const company =
      tagText(chunk, "dc:creator") || tagText(chunk, "author") || tagText(chunk, "company") || name;
    out.push({
      title,
      company,
      location: "Remote",
      url: url2,
      source: name,
      posted_at: tagText(chunk, "pubDate") || tagText(chunk, "published") || tagText(chunk, "updated"),
      desc: (tagText(chunk, "description") || tagText(chunk, "summary") || tagText(chunk, "content") || title).slice(0, 2000),
    });
  }
  return out;
}
