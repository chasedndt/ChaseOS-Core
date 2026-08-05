# Canonical Truth and Promotion

ChaseOS separates generated output from canonical truth. A note, runtime result, or agent draft is not canonical merely because it exists.

The rule that follows: **agents do not write facts into your knowledge base.** They produce
candidates. Candidates become knowledge only by passing a gate.

## Why this exists

The failure mode of a memory-equipped agent system is rarely dramatic. It is quiet
corruption: a plausible inference gets written down, is read back later as fact, informs the
next inference, and within weeks the knowledge base holds confident claims nobody verified
and nobody can trace.

Promotion closes that path by construction rather than by diligence.

## States

Content moves in one direction, and only across a gate:

```
Raw input  ──►  Draft  ──►  Reviewed evidence  ──►  Canonical knowledge
(captured,    (provisional,  (provenance enough      (promoted, relied on)
 untrusted)    useful)        to decide on)
             └──────────── promotion gate ───────────┘
```

- **Raw input:** captured material that has not been trusted or normalized.
- **Draft:** agent or operator work that may be useful but remains provisional.
- **Reviewed evidence:** output with enough provenance to support decisions.
- **Canonical knowledge:** promoted content that has passed the configured review/Gate.

Nothing skips a stage. An agent writing straight to canonical knowledge is not a shortcut;
it is the specific thing this design prevents.

## Promotion Requirements

- Clear source/provenance.
- Review or approval decision.
- Target path and reason for promotion.
- Evidence trail in logs or decision records.
- No private or credential residue when publishing to Core.

Provenance is enforced, not assumed:

```python
from runtime.gate_interface import check_provenance_minimums

ok, reason = check_provenance_minimums("02_KNOWLEDGE/note.md", frontmatter=None)
# ok is False — content without provenance cannot be promoted
```

## Promotion is an approval-class action

Canonical writeback is not an ordinary write. In a decision contract, setting
`canonical_writeback: "promote"` forces approval — and that approval must name an
accountable human and include an explicit human route step, or the route is blocked.

This is why [modality routing](Decision-Modality-Routing.md) and promotion are one story
from two angles: routing decides *who may act*; promotion decides *what may become true*.

## Quarantine

Untrusted input — web pages, documents, messages — lands in quarantine, not in knowledge.
Quarantine is a structural boundary, not a scan result: content leaves it only by deliberate
decision. A [scanner](Authority-and-Trust.md#untrusted-input-is-hostile-until-proven-otherwise)
can raise suspicion earlier, but a clean scan is not a promotion.

## The trade-off

This is slower than letting agents write freely, and that cost is real. What it buys is a
knowledge base where every canonical claim has a traceable origin and a named decision
behind it — and the ability to answer "why do we believe this?" months later.

## Related

- [Authority and Trust](Authority-and-Trust.md) — enforcement and provenance checks
- [Promotion Gate](../governance/Promotion-Gate.md) — gate configuration
- [Review Center](../governance/Review-Center.md) — the review surface
