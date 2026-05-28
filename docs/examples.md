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
