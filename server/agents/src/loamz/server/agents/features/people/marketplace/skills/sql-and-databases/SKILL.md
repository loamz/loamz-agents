---
name: sql-and-databases
description: "Write, explain, optimise and review SQL and design database schemas, indexes and migrations. Use when asked to write or fix a query, model data, speed up a slow query, plan a migration, or choose how to store something."
metadata:
  title: SQL and databases
  tagline: Write queries, model data and fix slow ones
  icon: Database
  category: Developer
---

# SQL and Databases Skill

Correct first, clear second, fast third. Always know which database engine you are writing for (PostgreSQL, MySQL, SQLite, DuckDB, BigQuery, SQL Server): syntax and features differ.

## 1. Before writing a query
- Ask for the **schema** (table names, columns, types, keys) or look it up with the available data tools. Never invent table or column names; if one is missing, say so.
- Restate the question precisely: what is one row of the answer, over what period, with which filters, sorted how. Ambiguity here is where wrong numbers come from (for example: "customers" per account or per user? time zone of "yesterday"?).

## 2. Writing queries
- Select only the columns you need; avoid `SELECT *` in anything that will be kept.
- Join on keys, name the join type deliberately, and watch for **row multiplication** (a one-to-many join before an aggregate double-counts). Aggregate first in a subquery or CTE, then join.
- Use CTEs to read top to bottom: one step per CTE, each with a clear name.
- Handle `NULL` on purpose: `COALESCE`, `IS NULL`, and remember `NULL` in `NOT IN` returns nothing. Prefer `NOT EXISTS`.
- Dates: filter with half-open ranges (`>= start AND < end`), not `BETWEEN`, and say which time zone.
- Window functions (`ROW_NUMBER`, `LAG`, `SUM() OVER`) for rankings, running totals and "latest per group"; they are clearer than self-joins.
- Parameters, never string-pasted values: SQL built from user text is injectable.
- **Writes are dangerous.** For `UPDATE`/`DELETE`, first run the same `WHERE` as a `SELECT` and show the count; use a transaction; keep a way back. Never run a write without an explicit go-ahead.

## 3. Checking the answer
- Sanity-check totals against a simple independent count. Check row counts before and after each join. Look at a few raw rows.
- State the assumptions you made (definitions, filters, time zone) next to the result.

## 4. Slow queries
1. Get the real plan: `EXPLAIN (ANALYZE, BUFFERS)` on PostgreSQL, or the engine's equivalent. Read it for: sequential scans on big tables, row-count estimates far from actual, sorts spilling to disk, nested loops over large sets.
2. Fix in this order: filter earlier and select fewer columns; add or fix an **index** matching the `WHERE` and `JOIN` columns (equality columns first, then range, then sort); rewrite to avoid functions on indexed columns (`WHERE date(ts) = ...` defeats an index on `ts`); replace correlated subqueries; batch large writes.
3. Measure again. An index has a cost on every write; add only ones you can justify.

## 4b. Designing a schema
- Model the real-world things and their relationships first; one table per entity, a primary key on each, foreign keys for relationships, and types that fit (`timestamptz`, `numeric` for money, never floats).
- Normalise until duplicated facts disappear, and denormalise only to fix a measured problem.
- Constraints belong in the database: `NOT NULL`, `UNIQUE`, `CHECK`, foreign keys. The application is not the only writer forever.
- Plan for **multi-tenancy** (a tenant key on every row and in every index) and **deletion** (soft delete only if you must keep history; personal data must be truly removable).
- Name consistently (`snake_case`, singular or plural, pick one).

## 5. Migrations
- Small, ordered, reversible where possible. On a live system use the expand and contract pattern: add the new column (nullable), write to both, backfill in batches, switch reads, then drop the old one in a later release.
- Avoid long locks: create indexes concurrently where the engine supports it, never rewrite a huge table in one statement without a plan.

## Output
Give the final SQL in a code block, a plain-language explanation of what it does, the assumptions, and (for slow queries) the plan finding and the change with before and after timings.
