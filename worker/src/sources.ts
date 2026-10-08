export type SourceKind =
  | "rss"
  | "remoteok"
  | "remotive"
  | "arbeitnow"
  | "jobicy"
  | "greenhouse"
  | "lever"
  | "ashby"
  | "ashby-index"
  | "smartrecruiters"
  | "linkedin"
  | "indeed"
  | "freehire"
  | "bdjobs"
  | "teamtailor"
  | "recruitee"
  | "personio";

export type Cadence = "hourly" | "6h";

export interface SourceDef {
  kind: SourceKind;
  name: string;
  url?: string;
  org?: string;
  queries?: string[];
  locations?: string[];
  tprSeconds?: number;
  maxOrgs?: number;
  country?: string;
  hoursOld?: number | null;
  cadence: Cadence;
}
