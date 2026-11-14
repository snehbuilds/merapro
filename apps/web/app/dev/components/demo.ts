/*
 * Gallery demo content, from the mockups: Acme, the Tech News group, the
 * Postgres 18 story, confidences 0.84 / 0.91 / 0.77 / 0.73, the 09:00–09:13
 * stagger. Instants are UTC; the workspace renders them in its timezone.
 */
import type { AccountRef } from "@/components/mera/account-chip";

export const TZ = "America/Los_Angeles";

/** A fixed "now" so the gallery and its tests render deterministically. */
export const NOW = "2026-11-08T21:18:00Z"; // 13:18 PST

export const accounts = {
  workLinkedIn: { id: "acc_work_li", platform: "linkedin", name: "Work LinkedIn", handle: "@alexmercer" },
  acmePage: { id: "acc_acme_page", platform: "linkedin", name: "Acme Page", handle: "Acme Inc" },
  xMain: { id: "acc_x_main", platform: "x", name: "X @main", handle: "@acme" },
  xTech: { id: "acc_x_tech", platform: "x", name: "X @tech", handle: "@acmedev" },
  acmeFb: { id: "acc_acme_fb", platform: "facebook", name: "Acme FB", handle: "Acme" },
  bluesky: { id: "acc_bsky", platform: "bluesky", name: "Bluesky", handle: "@acme.bsky.social" },
} as const satisfies Record<string, AccountRef>;

export const stagger = [
  { id: "s1", at: "2026-11-09T17:00:00Z", account: accounts.workLinkedIn, confidence: 0.84 },
  { id: "s2", at: "2026-11-09T17:04:00Z", account: accounts.acmePage, confidence: 0.91 },
  { id: "s3", at: "2026-11-09T17:08:00Z", account: accounts.xMain, confidence: 0.77 },
  { id: "s4", at: "2026-11-09T17:13:00Z", account: accounts.xTech, confidence: 0.73 },
] as const;

export const postgres = {
  title: "Postgres 18 ships async I/O",
  source: "postgresql.org",
  expiresAt: "2026-11-09T09:00:00Z", // 11h 42m after NOW
  excerpts: {
    workLinkedIn:
      "Postgres 18 ships async I/O, and the numbers on read-heavy workloads are worth a look.",
    acmePage:
      "Postgres 18 is out. The async I/O work is the headline for anyone running read-heavy…",
    xMain: "1/ Postgres 18 landed async I/O. On Linux the backend can issue reads without blocking.",
    xTech: "Should we be sizing read replicas differently now that PG18 has async I/O?",
  },
  bodies: {
    workLinkedIn: `Postgres 18 ships async I/O, and the numbers on read-heavy workloads are worth a look.

The short version: io_uring on Linux lets the backend issue reads without blocking, and the team is reporting meaningful gains on sequential scans.

What I'm watching: whether this changes how we size read replicas.

https://www.postgresql.org/about/news/…`,
    acmePage: `Postgres 18 is out. The async I/O work is the headline for anyone running read-heavy workloads: io_uring lets the backend issue reads without blocking, with meaningful gains reported on sequential scans.

Also shipping: OAuth support and skip-scan index improvements.

https://www.postgresql.org/about/news/…`,
    xMain: `1/ Postgres 18 landed async I/O. On Linux the backend can issue reads without blocking.

2/ Early numbers on sequential scans look meaningful on read-heavy workloads.

3/ Worth watching whether this changes how we size read replicas.
https://www.postgresql.org/about/news/…`,
    xTech: `Should we be sizing read replicas differently now that PG18 has async I/O?

io_uring means the backend stops blocking on reads. Early sequential-scan numbers suggest the answer is yes.

https://www.postgresql.org/about/news/…`,
  },
};

export const kafka = {
  title: "Kafka 4.0 drops ZooKeeper for good",
  source: "Hacker News",
  expiresAt: "2026-11-09T04:20:00Z", // 7h 02m after NOW
  stagger: { start: "2026-11-09T22:00:00Z", end: "2026-11-09T22:11:00Z" },
};

export const hiring = {
  title: "We're hiring a staff platform engineer",
  expiresAt: "2026-11-08T23:12:00Z", // 1h 54m after NOW
};
