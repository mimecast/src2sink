# Identity, versioning and boundaries — working notes

**Status:** unresolved design discussion, captured for review. **Nothing here is
filed as an issue yet** and no code should be written against it. Sections marked
**Measured** carry the actual output; everything else is proposal or open
question.

**Why this exists:** the 3.0 plan assumes a versioning model
([`metabase-versioning-design.md`](metabase-versioning-design.md), decisions
D1–D5). A review of that model raised four problems it does not survive
unchanged. This document records what was measured, which conclusions moved, and
what still needs deciding.

**How to read it:** §1–§4 are the four problems. §5 is what I got wrong during the
discussion, kept deliberately — knowing which conclusions shifted, and on what
evidence, is most of what makes the rest trustworthy. §6 lists candidate issues.
§7 is what still needs a decision from you.

---

## 1. Identity when git is not authoritative

### The case

History gets stripped: POC snapshots, exported tarballs, vendored copies,
`--depth 1` mirrors. The repo is real and scannable; there is no `.git`.

### Measured

```
no .git present:
  detect_git_sha -> None
  first scan  -> analysed
  second scan -> analysed   <-- incremental scan is dead
```

Fail-safe — it rescans rather than serving a stale record — but the incremental
scan is gone entirely. Worse for 3.0: the proposed repo-version key
`(repo, git_sha, detection_version, schema_version)` has a **null component**, so
content-addressing collapses and "storage grows with change, not with scan count"
stops being true. Every scan mints a new version of everything.

### Proposal

A **content-hash fallback**: hash the scanned file set when no sha is available.
The scan reads every file anyway, so the marginal cost is one hash per file.

Record *which* source produced the identity (`git-sha` | `content-hash`). They
are not interchangeable — the same tree yields different values — so switching
sources invalidates once, and the hash must cover only scanned files or a stray
build artefact causes spurious churn.

### The larger point this exposed

**Three axes were conflated into one.** Only the first is a hash:

| Axis | Question it answers | Source | Survives history-strip? |
|---|---|---|---|
| **Content identity** | is my cached record still valid? | git sha → content hash | hash: **yes**; sha: no |
| **Release identity** | which *published* version is this? | POM/build `<version>`, tags | **yes — it is in the file** |
| **Compatibility** | does A's call still bind to B? | signatures at each version | derived |

D1 used content identity for everything. Correct for caching. Useless for *"A
calls B and the signature changed"* — a sha tells you **that** something changed,
never **whether it still fits**.

Note the inversion for the POC case: **the declared version is more robust than
the sha.** A stripped export has no `.git` but still has `<version>2.3.1</version>`.

---

## 2. Versioning is about compatibility, not hashes

### Measured — the metabase is asymmetric

| Side | What is recorded |
|---|---|
| **Consumer** | `dependencies_internal` → `{groupId, artifactId, version, kind}` |
| **Provider** | identity index → `(group, name) → clone path`. **Coordinates without versions.** |

Nothing anywhere reads a repo's own `<version>`. Confirmed by scanning a repo
whose POM declares `2.3.1`: no top-level field contains it.

So the tool knows what every repo **consumes**, including which version, and does
not know what any repo **publishes**. The comparison cannot be made even in
principle.

### Why this matters

Fixing it is small and unlocks skew detection with **no historical scanning at
all**: consumer pins `warehouse-client:1.4.2`, provider declares `2.1.0` → a major
version behind, actionable immediately.

The original design assumed the useful answer required scanning historical
commits. It does not; that is enrichment, not the baseline.

### WIP drift — why declared version cannot be the identity

A feature branch may carry a stale POM: the branch says `2.3.1-SNAPSHOT` while
trunk moved to `2.4.0`. So the declared version is an **assertion about** the
content, which may be absent or wrong.

This is what keeps the layers separate and is worth stating as a rule:

> **Content identity is what was scanned. Labels are claims about it.**

A stale POM then produces a missing or wrong *label*, never a corrupted record.

---

## 3. Labels and collectors

### The model (yours, and better than D1)

Instead of "this record belongs to version X", each node carries a **set of
labels**, and a **collector** selects nodes by label predicate:

```
node  →  { tag:v1.4.2, tag:v1.5.0, branch:main@abc123, wip:feature-x@def456 }
```

### Why it beats record-per-version

**Deduplication where the redundancy actually is.** Most nodes do not change
between releases. Record-per-version stores 12 full copies of a mostly-static
repo's node set across 12 releases; labels store one set with 12 labels. Against
the 34 GB / 500 GB ceiling that is the difference between growth linear in
*releases* and linear in *change*.

**WIP isolation falls out free.** A feature branch gets `wip:feature-x@def456`,
never `tag:v1.4.2`. The release collector simply never selects it. Record-per-
version would need branch records special-cased.

**It suits the storage decision already taken.** Labels are a many-to-many join
table — the case relational engines are best at and graph databases are worst at.
This strengthens the existing SQLite recommendation rather than disturbing it.

### The archive answers the pinned-version question for free

The resolution chain is already three-quarters built:

| Step | Status |
|---|---|
| 1. Consumer POM → `warehouse-client:1.4.2` | **exists** — `dependencies_internal` carries it |
| 2. Coordinate → provider repo | **exists** — the identity index resolves it |
| 3. `1.4.2` → provider tag `v1.4.2` | **readable** — tags are plain text in `.git/refs/tags` and `packed-refs`, same read `detect_git_sha` uses |
| 4. → a repo version we already hold | **missing** — today `repos/<group>/<name>.json` is overwritten every run |

Step 4 is a **lookup, not a scan**. And it is the strongest argument for the
versioned model — stronger than "you can diff snapshots":

> The metabase becomes its own historical archive as a side effect of running.
> Scan the fleet weekly for a year and you accumulate the provider at many of the
> versions consumers actually pin. Resolution that would need a checkout becomes
> a free index lookup, and it improves over time with no extra work.

**The corrected skew ladder**, replacing D3's "never / on-demand / all":

| Basis | Cost | When |
|---|---|---|
| `pinned-archived` | free — index lookup | we already scanned the provider at that version |
| `pinned-declared` | free — compare declared versions | we have the provider at *some* version; report the skew |
| `head-fallback` | free | nothing better — **today's silent behaviour, now labelled** |
| `pinned-scanned` | expensive — checkout + scan | last resort, only for gaps measurement shows matter |

I had assumed the useful case needed the expensive rung. The first two are free.

**Two wrinkles.** Annotated tags point at a *tag object*, not a commit (every tag
in this repo is annotated), so tag→sha needs zlib-parsing a git object or the
`git` binary — avoidable entirely if each label records the declared version and
you look up by version. And tags live in `.git`, so they vanish in exactly the
stripped-snapshot case of §1 — another argument for declared version as the
primary key.

---

## 4. Dependencies: resolution, polyglot reality, and boundaries

### 4.1 Measured — declared versions are mostly unusable

```
literal version                      -> [('warehouse-client', '1.4.2')]        OK
property-interpolated                -> [('warehouse-client', '${warehouse.version}')]
inherited from parent                -> [('warehouse-client', '<empty>')]
BOM-managed (dependencyManagement)   -> [('platform-bom', '7.2.0'), ('warehouse-client', '<empty>')]
```

Only the literal case works. Properties, parent inheritance and BOM imports are
the **norm** in enterprise Maven, not the exception. The BOM case also emits
`platform-bom` as a dependency in its own right — a spurious edge.

So `dependencies_internal` records version *strings*, and outside the literal
case they are placeholders or empty. The "compare declared versions" rung of §3
rests on this being fixed.

### 4.2 Measured — resolution needs no network

The concern was that effective resolution implies `mvn`, which implies the
artifact repository, credentials, and downloading binaries. It does not, for the
part that matters:

```
identity index (coordinate -> clone path):
    ('com.example', 'platform-parent')            -> platform/platform-parent
    ('com.example.commerce', 'warehouse-client')  -> commerce/warehouse-service

  parent 'com.example:platform-parent' resolves on disk -> platform/platform-parent
  reading it gives warehouse.version = 1.4.2
  => consumer pins warehouse-client 1.4.2; provider HEAD declares 2.1.0
     resolved with no network, no downloads, no mvn.
```

**The fleet checkout *is* the artifact repository, for internal coordinates.** We
already clone every internal repo, so their POMs are on disk, and the identity
index that resolves coordinates to repos already exists. In that test the parent
POM lived in a *different repo* and resolved anyway.

| Tier | Source | Network |
|---|---|---|
| Same-file properties | text | no |
| In-repo parent (multi-module) | file read | no |
| Parent or BOM in another **internal** repo | identity index + file read | no |
| External parent/BOM (`spring-boot-starter-parent`) | artifact registry | **yes — stop here** |

The tier needing the network is the one we do not need: an external parent
governs *external* dependency versions, and those we do not care about.

**Caveat, stated honestly.** Reading a sibling repo's parent POM gives that repo
at **HEAD**, not at version 7.2.0. If its properties moved since, the value is
wrong — the same skew problem one level up. Mitigations: label the resolution
(`parent-resolved-at: head`) so it is never silently wrong, and once the archive
holds the parent at `tag:v7.2.0`, read that instead. The label archive pays off
here too.

**Scope shrinks further:** transitive internal dependencies need not be resolved
from the consumer. If A→B→C, B's own record carries B→C. Direct dependencies
suffice; the graph composes.

### 4.3 Measured — polyglot for identity, monoglot for dependencies

| | |
|---|---|
| **Identity** | polyglot — 9 ecosystems (Maven, Gradle, npm, Python, Rust, Go, PHP, .NET, Ruby) |
| **Dependencies** | Java + npm only — 4 parsers, nothing else |
| **Lockfiles** | **never read.** `yarn.lock` / `pnpm-lock.yaml` are touched at `repo_utils.py:224` purely to detect which build system is in use |

This inverts the priority. Designing cross-repo parent traversal for Maven — the
hardest ecosystem — while three ecosystems keep exact versions in a committed
file nobody reads:

| Ecosystem | Where exact versions live | Parsed today | Effort |
|---|---|---|---|
| **Go** | `go.mod` — exact, MVS, no ranges | no | trivial |
| **Python** | `uv.lock` / `poetry.lock` / pinned requirements | no | small |
| **npm/yarn/pnpm** | the lockfile, committed | `package.json` only, i.e. **ranges** | small |
| **Maven** | parent chain + properties + BOM | literal only | medium — §4.2 |
| **Gradle** | catalogue + `ext` + computed | catalogue only (`OI-3`) | hard ceiling — it is a program |

**The rule inverts to: read the lock where one exists; chase inheritance only
where none does.** The lockfile *is* the effective resolution — exact, committed,
offline. Maven needs the clever part only because it has no lockfile convention.

Two consequences to design in rather than discover:

* **Semver ranges break version equality.** `^1.4.2` names a *set*, not a version.
  Without a lockfile you can record only a constraint, so a collector may need to
  select by range satisfaction. npm and Python hit this constantly; Go and Maven
  mostly do not.
* **"Internal" is ecosystem-shaped.** `is_internal_coordinate` applies regexes to
  group/name, but only Maven/Gradle/npm coordinates ever reach it. A Go module
  path (`github.com/org/repo`) and a Python distribution name never get tested.

### 4.4 External libraries as a boundary catalogue

We are not building a dependency graph. We want **boundary metadata** — where
input enters and where it exits. Dependencies matter in three ways only:

- **Internal library** → a boundary to a repo we also scan → needs a **version**,
  to tell whether the boundary moved
- **External library** → identifies the **mechanism** of a boundary
  (`JdbcTemplate` → DB, `RestTemplate` → HTTP, `kafka-clients` → queue) → needs
  **identity**, never version
- **Plain IO** → filesystem, socket, process → no dependency involved

Version is needed only for internal coordinates — precisely the subset that
resolves offline. The scoping and the feasibility coincide, and no binary is ever
downloaded.

**Version is deliberately not tracked for external libraries.** "Dangerous if used
incorrectly" is about *usage*, not the CVE. Known-vulnerable-version analysis is
`pip-audit`/Dependabot's job and stays out of scope.

#### Measured — the mechanism exists, for one sink type

19 families are emitted — `sql`, `script-exec`, `file`, `data-store`,
`crypto-*`, `auth`, `http-in`, `http-out` and others — but **`SQL_DB_IMPORT_RX`
is the only library keyword list in the codebase**:

```python
SQL_DB_IMPORT_RX = r"\b(?:java\.sql|javax\.sql|jakarta\.persistence
   |org\.springframework\.jdbc|org\.hibernate|org\.jooq|mybatis
   |sqlalchemy|psycopg2?|pymysql|sqlite3|asyncpg
   |database/sql|gorm\.io|jmoiron/sqlx|knex|typeorm|sequelize)\b"
```

Already polyglot, already curated, already used as file-level evidence. Never
generalised.

Three gaps:

* **Only SQL has library corroboration.** `script-exec` is detected by call
  pattern alone; `file`, `data-store` and `crypto-*` likewise.
* **Deserialization has no family at all** — the archetypal "dangerous if used
  incorrectly": Jackson with polymorphic typing, SnakeYAML `Constructor`, Java
  `ObjectInputStream`, Python `pickle`, PHP `unserialize`. Same for LDAP, XPath,
  template engines, SSRF-capable clients.
* **Inputs are framework-annotation-only.** `HTTP_IN_RX` is keyed per framework
  bucket, so `@KafkaListener` and other message consumers, gRPC, GraphQL
  resolvers, file watchers, CLI arguments and environment are invisible. The tool
  currently sees one *kind* of front door.

#### Imports are the evidence channel; the manifest is not

An earlier proposal here was wrong and the correction matters:

> ~~The manifest is positive evidence, so it strengthens rather than loosens the
> `OI-7` guard.~~

**Manifest evidence is repo-scoped, and applying it to a file-level decision is a
scope error** — the same mistake `OI-7` was, one level up. A service with one JDBC
usage in one DAO would mark *every* file as having SQL evidence, and
`httpClient.execute(req)` in an unrelated package becomes a SQL sink again.
`file_has_sql_evidence` is already the correctly-scoped version.

Imports win on three counts:

* **They localise.** A file-scoped fact answers "where is the boundary" — what a
  source→sink path needs. "Somewhere in this repo" answers nothing.
* **File scope is a language guarantee**, not a heuristic: Java, Kotlin, Go and
  Python all scope imports to the file.
* **They reflect use, not declared intent.** A dependency can be inherited,
  unused or test-only. An import says *this code* touches that API — which also
  makes it immune to the WIP/stale-POM drift of §2.

The manifest keeps a role, but a different one — a **recall check**:

> "This repo declares `spring-jdbc` and we found no `sql` nodes."

Not grounds for emitting a node; grounds for suspecting the extractor missed one
or the dependency is unused. That is the §6 cross-cutting principle, and the shape
`service-call-unmatched.jsonl` already has. It is also the one case imports
genuinely miss: a driver loaded reflectively or wired by DI with no import.

| Channel | Scope | Role |
|---|---|---|
| **Import** | file | **evidence** — nodes may be emitted from this |
| **Manifest** | repo | **gap report** — never emits; only questions absence |

One vocabulary, two consumers, and only one may create findings.

---

## 5. What changed during this discussion

Kept deliberately: which conclusions moved, and on what evidence, is most of what
makes the rest trustworthy.

| Original position | What changed it | Now |
|---|---|---|
| Repo-version record is the unit (D1) | dedup argument + WIP isolation | node-level **labels**, collectors select by predicate |
| Skew needs historical scanning (D3) | the archive already holds scanned versions | free lookup first; scanning is the last resort |
| "The consumer side already has versions" | measured: property/parent/BOM all fail | version *strings*, usable only in the literal case |
| Effective resolution implies `mvn` + registry | measured: parent resolved offline from a sibling repo | offline for internal coordinates; external tier not needed |
| Maven is the representative ecosystem | measured: identity 9 ecosystems, deps 2, lockfiles 0 | **lockfile-first**; Maven is the outlier |
| Manifest is extra positive evidence | scope-error argument | manifest is a **recall check**, never evidence |
| Traversal is "later" (D5) | it is the tool's stated purpose | `OI-17`, P0 |

Seven positions, six changed by a measurement or an argument from scope. The one
constant is that reasoning from the shape of the problem, without measuring, was
wrong more often than it was right.

---

## 6. Candidate issues — not yet filed

| id | Candidate | Severity | Note |
|---|---|---|---|
| `OI-18` | Dependency versions unresolved where they *are* parsed | High | `${property}`, empty inherited, BOM false edge. Present-day defect |
| `OI-19` | Dependency parsing covers 2 of 9 ecosystems; no lockfile ever read | High | Go/Python/npm exact versions sit unread. `dependencies_internal: []` for a Go repo is indistinguishable from "no internal deps" |
| `OI-20` | Sink catalogue generalisation | High | Only SQL has library evidence; **deserialization has no family at all** |
| `OI-21` | Entry points are HTTP-annotation-only | High | Queue consumers, gRPC, GraphQL, file watchers, CLI, env all invisible |
| `OI-22` | No identity when git is absent | Medium | Incremental scan dies on stripped snapshots; content-hash fallback |
| `OI-23` | A repo's own declared version is never recorded | Medium | Half of every version comparison is missing |

`OI-20` and `OI-21` are both detection-recall issues and would change output, so
each requires a `DETECTION_VERSION` bump — which the gate will enforce, and which
correctly forces a fleet rescan.

---

## 7. Open questions — **answered 2026-08-05**, except Q6

Recorded as given, with the consequence of each spelled out so the decision can
be checked against what it implies. Q6 was badly posed and is restated below.

### Q1 — label set: **fixed kinds**

`tag:`, `branch:`, `wip:`, `env:` and nothing else without a code change.

*Consequence:* collectors can be validated at write time and the storage schema
can index the kind. A new kind becomes a reviewed change rather than something a
caller invents, which is the point.

### Q2 — collector: **stored named query, defined in config**

Not code, so adding one does not need a release.

*Consequence:* the definitions become a **configuration input**, like
`api-clients.json`. That brings the same obligations: a syntax error must be a
hard error rather than a silently empty result (the 1.1.0 empty-bindings defect),
and the loaded count belongs in the run manifest. Whether a collector change
forces a rescan needs deciding — it selects rather than extracts, so probably not,
which usefully keeps it out of `DETECTION_VERSION`.

### Q3 — range with no lockfile: **closest version with a matching signature, if available**

*Consequence:* this cannot be answered until `OI-17` records signatures per
version, so it degrades until then. The order is: exact archived version → closest
satisfying version whose signature set matches → closest satisfying version →
unresolved. The basis must be recorded, since "closest with matching signature"
is a materially weaker claim than "the pinned version".

### Q4 — provider endpoint deleted: **label as drift, keep it**

*Consequence:* the edge survives with a `drift` marker, so history is not
rewritten and stale consumers stay visible — which is the finding, not the noise.
Renderers must show the marker prominently; a low-fidelity edge rendered like any
other is worse than a dropped one.

### Q5 — parent POMs: **build a fleet-wide map of POM locations while traversing**

Find `pom.xml` (or XML files, reading the header and failing fast when it is not a
POM) during the fleet walk, so parents are retrievable by lookup afterwards.

*Consequence:* the walk already visits every file, so this is close to free, and
it removes the need to guess parent locations. Two things it does not solve, both
already recorded in §4.2: the located POM is that repo at **HEAD** rather than at
the pinned version, and an external parent is not in the fleet at all. Both stay
`unresolved`/labelled rather than guessed.

### Q6 — restated, because the original was unclear

**What I should have asked:** §4.4 proposes a *boundary catalogue* — one
declarative table mapping library identifiers to what they mean, generalising
`SQL_DB_IMPORT_RX`, which today is the only such list and covers SQL alone:

```
import pattern            ->  role      family            mechanism
org.springframework.jdbc  ->  sink      sql               database
com.fasterxml.jackson     ->  source    deserialization   untrusted input
org.apache.commons.exec   ->  sink      script-exec       process
```

Two things need deciding about it, and neither is about the format:

1. **Where does it live** — Python constants like `vocabulary.py` today, or a
   config file like `api-clients.json`? Config means additions need no release;
   constants mean they are code-reviewed and covered by the existing gates.
2. **What happens on an addition.** The catalogue is a *detection input*: a new
   entry changes what is detected, so it must bump `DETECTION_VERSION` and
   trigger a **fleet-wide rescan**. If it lives in config, additions become easy
   *and* expensive at the same time — the cost is invisible at the point of
   editing. That tension is the real question: how do we stop a one-line
   catalogue addition quietly triggering a full re-scan of the fleet?

### Q7 — non-HTTP entry points before reachability: **yes**

`OI-21` lands before `OI-17`.

*Consequence:* this is the sequencing change, and it reorders the 3.0 plan.
Reachability computed from an incomplete entry-point set produces confident,
incomplete answers — "no path from any entrypoint" when the entrypoint was a
`@KafkaListener` the tool cannot see. Phase 2 therefore becomes: entry-point
coverage → Kotlin AST parity (`OI-13`) → reachability. It makes Phase 2 longer
before anything is demonstrable, which is the cost of the decision and worth
naming.

---

## 8. What these answers change

* **`OI-21` moves ahead of `OI-17`** in the 3.0 plan (Q7). The plan needs
  updating; it currently sequences reachability first.
* **Collector definitions become a config input** (Q2), inheriting the
  hard-error-on-empty and manifest-count obligations that `api-clients.json` has.
* **Q3 depends on `OI-17`** — signature-matched version selection cannot exist
  before signatures do. Until then it degrades to "closest satisfying version".
* **A fleet-wide POM map** (Q5) is a new, cheap piece of the build phase, and it
  belongs with `OI-18` rather than in the 3.0 work.
* **Q6 is unresolved** and blocks nothing yet, but should be settled before the
  boundary catalogue is written rather than after.
