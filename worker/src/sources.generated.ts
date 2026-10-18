// GENERATED FILE -- do not edit by hand.
// Source: config/sources.yaml via worker/scripts/gen_sources.py
// Regenerate: python worker/scripts/gen_sources.py

import type { SourceDef } from "./sources.js";

// yaml_total=382 worker_supported=17 python_only=26 hourly=10

export const GENERATED_SOURCES: SourceDef[] = [
  {"kind": "linkedin", "name": "linkedin_geo_recent", "cadence": "6h", "industry": "general", "status": "unknown", "url": "https://www.linkedin.com/jobs/", "queries": ["Backend", "Full stack", "Software Engineer", "Forward Deployed Engineer", "Frontier Deployed Engineer"], "locations": ["Remote"], "tprSeconds": 43200, "hoursOld": 24},
  {"kind": "freehire", "name": "freehire-remote-tech", "cadence": "6h", "industry": "general", "status": "live", "url": "https://freehire.me/api/v1/agent/jobs/search", "queries": ["backend", "full stack", "software", "reactjs", "nodejs", "typescript", "javascript", "forward deployed engineer"], "locations": ["Remote"], "hoursOld": 24},
  {"kind": "bdjobs", "name": "bdjobs-bangladesh-tech", "cadence": "6h", "industry": "general", "status": "live", "url": "https://api.bdjobs.com/Jobs/api/JobSearch/GetJobSearch", "queries": ["backend", "full stack", "software", "reactjs", "nodejs", "typescript", "javascript", "forward deployed engineer"], "locations": ["Remote"], "hoursOld": 24},
  {"kind": "greenhouse", "name": "reddit", "cadence": "hourly", "industry": "software", "status": "live", "org": "reddit", "hoursOld": 24},
  {"kind": "greenhouse", "name": "samsara", "cadence": "hourly", "industry": "software", "status": "live", "org": "samsara", "hoursOld": 24},
  {"kind": "greenhouse", "name": "anthropic", "cadence": "hourly", "industry": "software", "status": "live", "org": "anthropic", "hoursOld": 24},
  {"kind": "greenhouse", "name": "grafana", "cadence": "hourly", "industry": "software", "status": "live", "org": "grafanalabs", "hoursOld": 24},
  {"kind": "rss", "name": "arbeitnow", "cadence": "hourly", "industry": "general", "status": "live", "url": "https://www.arbeitnow.com/api/job-board-api", "hoursOld": 24},
  {"kind": "rss", "name": "remotive", "cadence": "hourly", "industry": "general", "status": "live", "url": "https://remotive.com/api/remote-jobs?category=software-dev&limit=50", "hoursOld": 24},
  {"kind": "greenhouse", "name": "Chainlink Labs Careers", "cadence": "6h", "industry": "crypto", "status": "live", "org": "chainlink", "hoursOld": 24},
  {"kind": "rss", "name": "jobspresso", "cadence": "hourly", "industry": "general", "status": "live", "url": "https://jobspresso.co/jobs/feed/", "hoursOld": 24},
  {"kind": "rss", "name": "Remotive", "cadence": "6h", "industry": "general", "status": "live", "url": "https://remotive.com/api/remote-jobs", "hoursOld": 24},
  {"kind": "rss", "name": "cryptojobslist-feed", "cadence": "hourly", "industry": "crypto", "status": "live", "url": "https://api.cryptojobslist.com/jobs.rss", "hoursOld": 24},
  {"kind": "rss", "name": "jobspresso-feed", "cadence": "hourly", "industry": "general", "status": "live", "url": "https://jobspresso.co/jobs/feed/", "hoursOld": 24},
  {"kind": "recruitee", "name": "recruitee-make", "cadence": "6h", "industry": "software", "status": "live", "url": "https://make.recruitee.com", "hoursOld": 24},
  {"kind": "greenhouse", "name": "twilio", "cadence": "hourly", "industry": "software", "status": "live", "org": "twilio", "hoursOld": 24},
  {"kind": "recruitee", "name": "kodify", "cadence": "6h", "industry": "software", "status": "live", "url": "https://kodify.recruitee.com/api/offers/", "hoursOld": 24},
];

export interface PythonOnlySource {
  name: string;
  adapter: string;
  url: string;
  industry?: string;
  status?: string;
  hoursOld?: number | null;
}

// Cannot run on Workers (TLS impersonation / RSA / browser). Stays on Python/Actions.
export const PYTHON_ONLY_SOURCES: PythonOnlySource[] = [
  {"name": "naukri-india-remote", "adapter": "naukri", "url": "https://www.naukri.com/", "industry": "general", "status": "live", "hoursOld": 24},
  {"name": "CryptoJobs.com", "adapter": "web", "url": "https://cryptojobs.com/", "industry": "crypto", "status": "live", "hoursOld": 24},
  {"name": "Solana Ecosystem Jobs", "adapter": "web", "url": "https://jobs.solana.com/", "industry": "crypto", "status": "live", "hoursOld": 24},
  {"name": "Web3internships", "adapter": "web", "url": "https://www.web3internships.com/", "industry": "crypto", "status": "live", "hoursOld": 24},
  {"name": "Web3.career", "adapter": "web", "url": "https://web3.career/", "industry": "crypto", "status": "live", "hoursOld": 24},
  {"name": "CryptoJobsList", "adapter": "web", "url": "https://cryptojobslist.com/", "industry": "crypto", "status": "live", "hoursOld": 24},
  {"name": "DeFi Jobs", "adapter": "web", "url": "https://defi.jobs/", "industry": "crypto", "status": "live", "hoursOld": 24},
  {"name": "BitcoinJobs", "adapter": "web", "url": "https://bitcoinjobs.com/", "industry": "crypto", "status": "live", "hoursOld": 24},
  {"name": "CryptoJobs", "adapter": "web", "url": "https://crypto.jobs/", "industry": "crypto", "status": "live", "hoursOld": 24},
  {"name": "Remote3", "adapter": "web", "url": "https://remote3.co/", "industry": "crypto", "status": "live", "hoursOld": 24},
  {"name": "Web3 Jobs", "adapter": "web", "url": "https://web3jobs.ai/", "industry": "crypto", "status": "live", "hoursOld": 24},
  {"name": "Crypto Careers", "adapter": "web", "url": "https://crypto-careers.com/", "industry": "crypto", "status": "live", "hoursOld": 24},
  {"name": "Aave Careers", "adapter": "web", "url": "https://aave.com/careers/", "industry": "crypto", "status": "live", "hoursOld": 24},
  {"name": "Wellfound (formerly AngelList)", "adapter": "web", "url": "https://wellfound.com/", "industry": "general", "status": "live", "hoursOld": 24},
  {"name": "RemoteLeaf", "adapter": "web", "url": "https://remoteleaf.com/", "industry": "general", "status": "live", "hoursOld": 24},
  {"name": "RemoteMore", "adapter": "web", "url": "https://remotemore.com/", "industry": "general", "status": "live", "hoursOld": 24},
  {"name": "Dice", "adapter": "web", "url": "https://www.dice.com/", "industry": "general", "status": "live", "hoursOld": 24},
  {"name": "WUZZUF", "adapter": "web", "url": "https://wuzzuf.net/", "industry": "general", "status": "live", "hoursOld": 24},
  {"name": "Eurojobs", "adapter": "web", "url": "https://www.eurojobs.com/", "industry": "general", "status": "live", "hoursOld": 24},
  {"name": "TokyoDev", "adapter": "web", "url": "https://www.tokyodev.com/", "industry": "general", "status": "live", "hoursOld": 24},
  {"name": "https://www.jobberman.com/jobs?q=blockchain", "adapter": "web", "url": "https://www.jobberman.com/jobs?q=blockchain", "industry": "crypto", "status": "live", "hoursOld": 24},
  {"name": "Chainlink Labs Careers", "adapter": "web", "url": "https://chainlinklabs.com/careers", "industry": "crypto", "status": "live", "hoursOld": 24},
  {"name": "Coinbase Careers", "adapter": "web", "url": "https://www.coinbase.com/careers", "industry": "crypto", "status": "live", "hoursOld": 24},
  {"name": "Ripple Careers", "adapter": "web", "url": "https://ripple.com/careers/", "industry": "crypto", "status": "live", "hoursOld": 24},
  {"name": "Remotive", "adapter": "web", "url": "https://remotive.com/", "industry": "general", "status": "live", "hoursOld": 24},
  {"name": "web3-career-jsonld", "adapter": "web", "url": "https://web3.career/", "industry": "crypto", "status": "live", "hoursOld": 24},
];
