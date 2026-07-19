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
