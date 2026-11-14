# ADR-0007: pgvector rather than a dedicated vector database

**Status:** Accepted
**Date:** 2026-11-08

## Context

Mera uses embeddings in three places:

1. **Semantic deduplication** of ingested content, which is the highest-value use. Three
   feeds covering one story should produce one draft, not three.
2. **Relevance scoring** against content pillars, by cosine similarity.
3. **Duplicate-post prevention**, comparing a new draft against the last 90 days of
   published posts. This one also matters for platform compliance, since X's automation
   rules prohibit substantially similar repeated content.

Expected volume at the documented posture: low thousands of content items per month, so
tens of thousands of vectors per workspace per year. Every query is filtered by
`workspace_id` first and usually by a recency window as well.

## Decision

`pgvector` in the same PostgreSQL instance, with an HNSW index using `vector_cosine_ops`.

```sql
CREATE INDEX content_item_embedding_hnsw
  ON content_item USING hnsw (embedding vector_cosine_ops);
```

Dimension is pinned per embedding model in configuration; changing models is a migration
that re-embeds rather than a silent switch.

## Consequences

**Good**

- **No second datastore**, consistent with [ADR-0002](0002-single-postgres-datastore.md).
- **Joins work.** A similarity query can filter by `workspace_id`, `source_id`,
  `fetched_at`, and `duplicate_of IS NULL`, and return full rows in one statement. With an
  external store this is two round trips and a client-side join, and the pre-filter is
  either unavailable or implemented as a metadata filter that is less expressive than SQL.
- **The filters do the heavy lifting.** Scanning hundreds of recent rows in one workspace
  is fast regardless of index sophistication, which is the actual query shape here.
- **Transactional consistency.** The embedding and its row commit together. No window
  where a content item exists without its vector, which with an external store is a real
  state that dedupe has to handle.
- **One backup.** Vectors are in the dump.

**Bad**

- **Throughput ceiling.** pgvector is competitive to roughly the low millions of vectors;
  past that a purpose-built store wins clearly. Several orders of magnitude above the
  expected volume, but a real ceiling.
- **HNSW index build is memory-hungry** and slow on large tables. Not an issue at this
  size; worth knowing before a bulk backfill.
- **Index maintenance cost on write.** HNSW inserts are more expensive than B-tree
  inserts. Irrelevant at tens of inserts per hour.
- **Changing embedding model means re-embedding everything.** Dimension is baked into the
  column type. This is true of any vector store, but it is worth stating because it makes
  model choice a semi-permanent decision.
- **Fewer tuning knobs** than a dedicated engine: no quantization options, no built-in
  hybrid search ranking, no separate index tiers.

## Alternatives considered

**Qdrant.** Excellent, well-designed filtering, good operational story. Rejected on the
same grounds as Redis in ADR-0002: a second datastore whose capabilities exceed what this
workload needs, costing a service to run, back up, and keep consistent with Postgres.

**Pinecone or another managed service.** Removes operational burden, adds a network hop,
a monthly cost, a vendor dependency, and an external data-residency consideration for a
self-hosted product. Wrong trade for something that must run on one machine.

**pgvectorscale.** The StreamingDiskANN extension, which would raise the ceiling
substantially while staying in Postgres. Not needed now and available later without any
application change, which makes it the natural first escalation step rather than an
alternative to evaluate today.

**No embeddings, URL and title matching only.** Genuinely considered, because exact URL
hashing catches most duplicates for free. Rejected because the case that matters is two
publications covering one story in different words, and that is exactly what URL matching
cannot see. Semantic dedupe is the feature; removing it would remove the reason the
ingestion pipeline is worth having.

## Revisit when

- Vector count per workspace approaches the low millions.
- Similarity queries appear in `pg_stat_statements` among the top queries by total time.
- HNSW index build time during a backfill becomes operationally awkward.
- A use case appears needing hybrid dense-and-sparse ranking beyond what combining
  `tsvector` and `pgvector` in SQL can express.

The first escalation is **pgvectorscale**, not a migration. It is an extension in the same
database and requires no application change. Only if that is insufficient does an external
store become justified, and by then the volume would make it obviously correct.
