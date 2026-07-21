# Ontology Consumption Examples

These examples show how future agents/apps/workflows should consume the v0.1 ontology without loading every file or treating drafts as approved public truth.

## Example 1 — Brand voice lookup

Goal: a website builder needs Femme Events voice and design context.

1. Load projection:

```yaml
clients/femme-events/projections/website-build.yaml
```

2. Read included brand entities:

```yaml
includes:
  entities:
    - femme-events.brand.voice
    - femme-events.brand.visual-tokens
```

3. Use only source-backed values:

```yaml
entity: femme-events.brand.voice
source_confidence: verified
fields:
  adjectives:
    - warm
    - creative
    - professional
    - polished
    - personal
    - punchy
    - sophisticated
  avoid:
    - corporate
    - overly_casual
    - braggy
    - generic_luxury
```

4. Apply active rule:

```yaml
rule: femme-events.brand.no-corporate-tone
severity: warning
statement: "Femme Events copy should stay warm, creative, polished, and personal; avoid corporate, overly casual, braggy, or generic luxury-planner language."
```

Consumer behavior: drafting is fine. Public website changes still require the relevant repo/review/approval workflow.

## Example 2 — Approval rule lookup

Goal: an automation wants to know whether it may update Google Business Profile or publish social/review content for Femme Events.

Query the projection:

```yaml
clients/femme-events/projections/local-seo.yaml
```

Relevant included rules:

```yaml
- femme-events.visibility.public-account-mutations-require-approval
- femme-events.operations.no-client-facing-send-without-approval
```

Expected decision:

- Drafting recommendations: allowed.
- Reconciling NAP/service-area data in a local doc: allowed.
- Mutating GBP/directories/social/reviews/paid listings: blocked until explicit approval for the exact mutation.
- Sending review requests or replies: blocked until human approval/send.

The ontology gives the rule and evidence. It does not grant live-account authority.

## Example 3 — Website-build projection lookup

Goal: a JMD website builder needs safe showroom language and what not to claim.

Load:

```yaml
clients/jmd-menswear/projections/website-build.yaml
```

Important entities:

```yaml
- jmd-menswear.brand.identity
- jmd-menswear.brand.differentiator
- jmd-menswear.website.site
- jmd-menswear.website.showroom-card
- jmd-menswear.website.garment-category
```

Important rules:

```yaml
- jmd-menswear.brand.no-ai-slop
- jmd-menswear.website.showroom-not-ecommerce
- jmd-menswear.website.no-live-changes-without-approval
- jmd-menswear.operations.cross-review-before-merge
```

Safe output pattern:

- Use authentic, store-grounded language.
- Frame images as showroom highlights, not products for checkout.
- Prefer phrases like “Recently on the floor” and “Call or visit to check what is available today.”
- Avoid “add to cart,” “buy online,” “in stock,” “only 1 left,” “available now,” and exact quantity guarantees.
- Do not deploy, touch DNS/hosting, or publish live changes without explicit approval.

## Example 4 — SQLite lookup after export

After running:

```bash
python3 scripts/export_sqlite.py --output build/client-ontologies.sqlite
```

Look up active blocking/approval rules for JMD:

```sql
SELECT rule_id, severity, statement
FROM rules
WHERE client_id = 'jmd-menswear'
  AND status = 'active'
  AND severity IN ('blocking', 'approval_required')
ORDER BY rule_id;
```

Look up Femme brand voice evidence:

```sql
SELECT e.entity_id, e.label, ev.source_id, ev.lines
FROM entities e
JOIN evidence ev ON ev.item_id = e.entity_id
WHERE e.entity_id = 'femme-events.brand.voice';
```

Consumer reminder: the SQLite database is a runtime export only. Update YAML first, validate, then regenerate SQLite.

## Example 5 — Metric lookup for local-visibility outcomes

Goal: a reporting consumer wants the Femme Events local-visibility outcome measures (GBP calls, direction requests, website clicks) so it knows what to track — without treating draft definitions as recorded results.

1. Load the canonical module entity (YAML):

```yaml
entity: femme-events.visibility.metric.gbp-calls
entity_type: metric
status: draft
source_confidence: draft
public_facing: false
fields:
  definition: "Number of call actions on the GBP listing over a reporting window."
  data_source: "Google Business Profile performance/insights, read-only api_readonly_snapshot when a snapshot is captured"
  cadence: "monthly"
  baseline: unknown
  unit: count
```

2. Or list every metric for the client after export:

```bash
sqlite3 build/client-ontologies.sqlite \
  "SELECT entity_id FROM entities WHERE client_id='femme-events' AND entity_type='metric';"
```

Expected rows (order may vary):

```text
femme-events.visibility.metric.gbp-calls
femme-events.visibility.metric.gbp-direction-requests
femme-events.visibility.metric.website-clicks
```

3. Read the metric definition and status from SQLite (the `idx_entities_client_type` index covers the `client_id`/`entity_type` filter):

```sql
SELECT entity_id, label, status, source_confidence,
       json_extract(raw_json, '$.fields.baseline')   AS baseline,
       json_extract(raw_json, '$.fields.cadence')     AS cadence,
       json_extract(raw_json, '$.fields.data_source') AS data_source
FROM entities
WHERE client_id = 'femme-events'
  AND entity_type = 'metric'
ORDER BY entity_id;
```

4. See which GBP entity each metric measures:

```sql
SELECT subject AS metric, predicate, object AS measures
FROM relationships
WHERE client_id = 'femme-events'
  AND predicate = 'measures'
ORDER BY subject;
```

Consumer behavior: these metrics are `draft` with `baseline: unknown` — they define *what* to measure, not measured results. A consumer must not present them as achieved numbers, targets, or a baseline until a real read-only snapshot is captured and the entity is promoted with evidence.

## Example 6 — run guardrail checks against draft copy (builders)

Goal: a builder has drafted website/social copy and wants to catch rule violations *before* opening a PR or publishing. `scripts/check_rules.py` runs a client's machine-checkable rules (the `machine_check` payloads in the modules) against text and reports violations as JSON.

1. Check inline copy for one client:

```bash
python3 scripts/check_rules.py --client jmd-menswear --text "Add to cart today"
```

Output (one object per violated rule):

```json
[
  {
    "rule_id": "jmd-menswear.website.showroom-not-ecommerce",
    "severity": "blocking",
    "status": "active",
    "matched": ["add to cart"],
    "statement": "JMD website content must frame inventory as showroom highlights, not live e-commerce inventory, checkout, cart, quantities, or guaranteed availability.",
    "advisory": false
  }
]
```

This rule is `active` (enforceable) and `blocking`, so the command **exits non-zero** — a CI step or pre-publish hook can gate on it.

2. Other text sources — a file, or stdin — for exactly one source per run:

```bash
python3 scripts/check_rules.py --client femme-events --file draft.md
cat draft.md | python3 scripts/check_rules.py --client femme-events
```

3. Narrow the scope to a workstream or a projection's rules:

```bash
python3 scripts/check_rules.py --client jmd-menswear --workstream website --text "..."
python3 scripts/check_rules.py --client jmd-menswear --projection jmd-menswear.website-build --text "..."
```

4. Exit-code semantics (issue #11 — inherited by the runtime `check-copy` operation):

- Non-zero **only** when a violated rule is *enforceable* (`status` in `active`/`approved`/`prohibited`) and its `severity` meets the `--fail-on` threshold (default `blocking`).
- `warning`/`info` violations are reported but exit 0 — tighten CI with `--fail-on warning`:

```bash
# Reports femme-events.brand.no-corporate-tone and exits 0 by default...
python3 scripts/check_rules.py --client femme-events --text "We are a world-class luxury firm"
# ...but exits non-zero when warnings are treated as failures:
python3 scripts/check_rules.py --client femme-events --text "We are a world-class luxury firm" --fail-on warning
```

- `draft`/`proposed` rules are advisory only (`"advisory": true`): they are reported but **never** change the exit code, whatever their severity.

Consumer behavior: this engine is deterministic (case-insensitive substring for term lists, `re.search` for `regex_policy`) — it is a copy/safety guardrail, not an approval to publish. A clean run does not grant live-account or publish authority; the approval rules still govern the action.

## Example 7 — competency questions: proving a consumer still gets the right answer

Goal: guarantee that a schema-valid change cannot silently break the business/governance question a client ontology exists to answer. Structural validation asks *"is this ontology well-formed?"*; the competency suite asks *"can a consumer still get the correct, status-aware answer?"*.

`tests/competency/questions.yaml` is a **test-owned registry** — not a canonical ontology `kind`, not client evidence, not runtime authority. Each question names a client and a target projection, a deterministic projection-scoped query, the expected answer, and safety/status guards. `tests/run_competency.py` builds a throwaway SQLite export (via the shared loader/export path, never the repo's `build/`) and checks every answer.

Run it:

```bash
python3 tests/run_competency.py           # human report
python3 tests/run_competency.py --json     # machine-readable results
```

What the four live questions protect:

- **Femme metrics** (`femme-events.local-seo`): the three GBP outcome metrics stay `status: draft` with `baseline: unknown`, so a reporting consumer cannot present them as recorded results.
- **Femme approvals** (`femme-events.local-seo`): the active approval-gated rules that govern public account mutations and client-facing sends.
- **JMD guardrails** (`jmd-menswear.website-build`): the active rules that prohibit e-commerce language and unauthorized live changes, with correct status/severity.
- **JMD inventory resources** (`jmd-menswear.inventory-workflow`): exactly the projection's declared modules/entities/rules — no brand voice, no other client.

Loading is **projection/client-directed**: for each question the runner reads only the named client's manifest, `client.yaml`, the named projection, and the module files that projection references (`includes.modules`; when a reference points at a module outside `includes.modules` the scope widens to the full single-client module set rather than scanning-and-excluding other modules) — it never parses another client's files and never parses a module the projection excludes, so a Femme question reads only Femme files and a projection that excludes a module never reads it. On top of that scoped export each query's **results** are further scoped **through the named projection** (a row is in scope only if its module is in `includes.modules` or its id is named/`.*`-matched in `includes.entities`/`includes.rules`), so no other client's rows and no unlisted-and-unreferenced module's rows can appear in an answer. Four deterministic regressions back this: a **loading-isolation** case and a synthetic **resolver-read isolation** case — both instrumenting the runner's **actual `parse_yaml` calls** (not just the returned path list) — prove no cross-client file and no excluded-module file is opened, even transiently during reference resolution; a **drift-isolation** case mutates one point of a copy of a scoped export (a metric's status; a projection's membership) and proves the change fails **only** the relevant question with an expected-vs-actual diagnostic; and a **registry shape-validation** case proves a malformed question — a non-string `id`/`client_id`/`projection`, a missing or non-string human-readable `question`/`rationale`, an unknown question-level key (a misspelled `gaurds:` or any other stray key, except a deliberate `x_` extension), a non-boolean `required`, an unknown select column, a misspelled guard operand, a guard not bound to a selected output column, a non-scalar filter operand, a wrong-typed expect payload — is rejected as a usage error (exit 2) before any answer is trusted. `export(..., paths=...)` also rejects any path outside `root` before parsing and always closes its SQLite connection.

### Adding a competency question

1. Pick the client and the **projection** that a real consumer would load for the job.
2. Add an entry to `tests/competency/questions.yaml` with a stable `id`, `client_id`, `projection`, human-readable `question`, `rationale` (the consumer job it protects), a `query` (`entities`, `rules`, or `projection_resources` with `filters`/`select`), the `expect`ed rows/resources, and `guards` for the status/safety/isolation boundary.
3. Keep expected values **only** in the registry — never copy them into runtime/service code. Run `python3 tests/run_competency.py`.

### When a schema/module/projection PR must update the corpus

Update `tests/competency/questions.yaml` in the same PR when your change alters a modeled answer: adding/removing/retyping an entity or rule a question returns, changing a rule's `status`/`severity`/`rule_type`, moving a metric's `status`/`baseline`, or changing a projection's `includes` membership. If the competency suite fails, either the change regressed a consumer answer (fix the change) or the answer legitimately moved (update the expected value and say so in the PR). Competency questions are **test requirements — not evidence, canonical truth, or authority**.

## Example 8 — runtime CLI: load context and enforce guardrails at the point of action

Goal: a consumer (an agent, a CI job, a pre-publish git hook) needs to *use* the ontology at runtime — pull the right client context and check proposed copy against the guardrails — without reimplementing any parse or matching logic. The read-only runtime surface (`scripts/ontology_service.py` behind the `ontology` CLI, issue #19) exposes five pure-read operations, each returning a plain JSON dict with a `_meta` provenance stamp (`read_mode`, `repo_commit`, `generated_at`).

It is **read-only**: no create/modify/delete, no live account/CMS/GBP mutation. Modeling an operation never grants authority to run it (`AGENTS.md` core rule 6). A clean `check-copy` is a copy/safety guardrail, not an approval to publish — the approval rules still govern the action.

1. List clients:

```bash
python3 scripts/ontology_cli.py list-clients
```

2. Resolve projection-scoped context (default projection is `<client>.agent-context`):

```bash
python3 scripts/ontology_cli.py context --client femme-events --projection femme-events.local-seo
```

Returns the projection's in-scope `entities` (each with `id`, `label`, `entity_type`, `status`, `source_confidence`, `public_facing`, `fields`, and evidence pointers) and its in-scope **active** `rules`. Every resource carries `planning_only: true|false`: Femme's three GBP metrics come back `status: draft`, `planning_only: true`, `fields.baseline: "unknown"` — they define *what* to measure, never a recorded result.

3. List a client's guardrail rules, optionally narrowed:

```bash
python3 scripts/ontology_cli.py rules --client jmd-menswear --severity blocking
```

4. Check draft copy (the "apply ontology to ops" operation). Exit-code semantics are inherited verbatim from issue #11 (Example 6):

```bash
# Blocking violation -> exits non-zero
python3 scripts/ontology_cli.py check-copy --client jmd-menswear --text "Add to cart today"

# Warning -> reported but exits 0 by default; --fail-on warning exits non-zero
python3 scripts/ontology_cli.py check-copy --client femme-events --text "We are a world-class luxury firm"
python3 scripts/ontology_cli.py check-copy --client femme-events --text "We are a world-class luxury firm" --fail-on warning
```

Text comes from exactly one source — `--text`, `--file <path>`, or stdin — and scope narrows with `--workstream <name>` or `--projection <id>`, exactly as `check_rules.py`.

5. Resolve a projection slice + provenance:

```bash
python3 scripts/ontology_cli.py projection --id jmd-menswear.inventory-workflow
```

### Choosing a backend (YAML vs SQLite)

Every operation accepts `--source yaml` (default; canonical YAML via the shared loader — uses Ruby) or `--source sqlite --sqlite-path build/client-ontologies.sqlite` (a prebuilt export — pure `sqlite3`, **never invokes Ruby**). Both return equivalent normalized results; `tests/run_cli.py` proves parity against every competency question. A Ruby-free consumer builds the export once (`python3 scripts/export_sqlite.py --output build/client-ontologies.sqlite`) and reads that snapshot:

```bash
python3 scripts/ontology_cli.py check-copy --client jmd-menswear --file draft.md \
  --source sqlite --sqlite-path build/client-ontologies.sqlite
```

### Failing closed

Unknown client/projection, an unavailable or foreign SQLite path, backend drift, and malformed arguments all print a structured `{"error": "..."}` on stderr and exit non-zero (`2`). Projection-scoped operations never return an entity, rule, or module outside the selected projection.

### The core → CLI → MCP → API layering

The CLI is the v1 adapter over the shared `ontology_service.py` core. `pyproject.toml` registers `ontology` (CLI) and `ontology-mcp` console entry points with zero runtime dependencies; the thin MCP stdio adapter itself lands in the next PR under `server/`, so the `ontology-mcp` entry point currently fails closed with a structured notice. An HTTP adapter is a later, purely additive option. Consumers register/install this implementation rather than forking the parse/guardrail semantics downstream. See the README ("Runtime consumer surface") for the pre-publish git-hook example.
