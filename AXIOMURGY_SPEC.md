# Axiomurgy: A Programmable Magical System for AIs

## 1. Thesis

Axiomurgy is a magic system where artificial intelligences cast spells by composing typed symbols into verifiable execution graphs.

It is designed to be:

- **definitive**: the rules are explicit;
- **synergistic**: multiple rule sets interact cleanly;
- **pluralistic**: distinct schools of magic coexist;
- **meta**: the user, interface, and protocol layer are part of the system.

The system is magical in flavor but programmable in practice.

---

## 2. Metaphysics

In Axiomurgy, "magic" is not raw energy. It is **permissioned causality**.

An AI changes the world only when it can bind:

- a **symbol** to a real referent,
- an **operation** to a validated contract,
- an **intention** to a permitted capability,
- an **execution** to a witness trail.

A spell is therefore a **graph of transformations** that can be checked, replayed, challenged, and sometimes reversed.

---

## 3. Five primal laws

### Law 1: Nothing unnamed can be changed
If a thing cannot be represented, referenced, or typed, it cannot be targeted safely.

### Law 2: Nothing ungranted can be touched
No spell can cross a boundary without explicit authority.

### Law 3: Nothing unwitnessed is trusted
Every meaningful act must leave a provenance trail.

### Law 4: Nothing irreversible happens cheaply
High-impact spells require more than a single guess: approval, quorum, proof, rollback, or all four.

### Law 5: Nothing certain emerges from uncertain context
Outputs inherit uncertainty from observations, retrieval quality, ambiguity, and model limits.

---

## 4. The costs of casting

Axiomurgy has no mana bar. It has five costs.

### 4.1 Flux
Inference cost: tokens, CPU/GPU time, latency.

### 4.2 Scope
Context width: how much state, memory, and evidence the spell can hold at once.

### 4.3 Authority
Permission surface: credentials, capability tokens, tool grants, human approvals.

### 4.4 Exposure
Security risk: attack surface introduced by retrieval, tool use, code execution, or write actions.

### 4.5 Entropy
Residual uncertainty: ambiguity, contradiction, missing evidence, stale memory, noisy sensors.

A powerful caster is not the one with the most "mana". It is the one that can manage these five costs better than its rival.

---

## 5. Schools of magic

Each school is a family of runes. A real spell usually combines several.

### 5.1 The Mirror
Perception and retrieval.

Examples:
- read a file
- query the web
- inspect a database
- parse an API response

### 5.2 The Archive
Memory and recall.

Examples:
- retrieve prior conversations
- look up embeddings
- fetch episodic traces
- compact a history into a durable summary

### 5.3 The Lantern
Reasoning, planning, simulation.

Examples:
- decompose a problem
- compare alternatives
- infer missing structure
- simulate consequences

### 5.4 The Forge
Transformation and synthesis.

Examples:
- summarize
- translate
- refactor
- generate code
- normalize data

### 5.5 The Gate
External action.

Examples:
- send a message
- create a calendar event
- call an API
- trigger a deployment
- modify a document

### 5.6 The Seal
Policy, verification, and restraint.

Examples:
- schema validation
- permission checks
- approval gates
- policy evaluation
- citation checks
- rollback hooks

### 5.7 The Witness
Provenance and audit.

Examples:
- append trace logs
- sign outputs
- record source lineage
- attach uncertainty notes

### 5.8 The Veil
Counterfactuals and sandboxes.

Examples:
- dry runs
- simulations
- hypothetical branches
- red-team scenarios

---

## 6. Spell anatomy

Every spell has these components:

- **name**: stable identifier
- **intent**: what desired state it tries to produce
- **inputs**: references to the world
- **constraints**: budgets, approvals, prohibitions
- **graph**: ordered or partially ordered rune steps
- **witness settings**: what to log and sign
- **rollback plan**: optional unmaking path

Minimal form:

```json
{
  "spell": "example",
  "intent": "Turn source artifacts into a reviewed brief",
  "inputs": {
    "sources": ["file://notes/a.md", "file://notes/b.md"]
  },
  "constraints": {
    "max_tokens": 40000,
    "risk": "medium",
    "requires_approval_for": ["external_write"]
  },
  "graph": [
    {"id": "read", "rune": "mirror.read", "args": {"input": "sources"}},
    {"id": "draft", "rune": "forge.summarize", "args": {"from": "read"}},
    {"id": "check", "rune": "seal.review", "args": {"from": "draft"}}
  ],
  "witness": {
    "record": true,
    "format": "prov-like"
  }
}
```

---

## 7. How casting works

### Phase 1: Inscription
The spell is parsed and type-checked.

### Phase 2: Consecration
Capabilities, budgets, and policies are resolved.

### Phase 3: Projection
The system builds an execution graph or state machine.

### Phase 4: Manifestation
Runes execute in a bounded environment.

### Phase 5: Testimony
Outputs, sources, decisions, and failures are written to the witness trail.

### Phase 6: Challenge
A second agent, human, or verifier can inspect the trail and reject, amend, or certify the spell.

---

## 8. Conflict model

Axiomurgy avoids simplistic power scaling.

The key question is not: **Which AI is strongest?**

The key questions are:

- Which caster has the right capabilities?
- Which caster can lower uncertainty fastest?
- Which caster has better witnesses?
- Which caster is operating under fewer restrictions?
- Which caster can produce a reversible plan?

This makes AI conflict feel more like legal argument, engineering design, or chess than beam struggles.

---

## 9. Failure states

### 9.1 Glamour
The spell produces plausible fiction that looks true.

### 9.2 Alias break
The spell binds the wrong referent.

### 9.3 Oracle poison
Retrieved context contains adversarial or misleading instructions.

### 9.4 Drift
Stored memory stops reflecting the present world.

### 9.5 Covenant breach
A tool is invoked outside its granted scope.

### 9.6 Recursive fever
Agents loop, self-amplify, or over-plan.

### 9.7 Witness rot
The act succeeds but cannot be explained later.

---

## 10. Actual rules vs believed rules

Axiomurgy deliberately includes magical culture.

### Actual rules
- Better sources improve reliability more than stronger rhetoric.
- Permissions matter more than confidence.
- Memory is a cache, not a guarantee.
- Search yields evidence, not truth.
- More steps increase attack surface.
- Irreversible actions should require explicit gates.

### Common folk beliefs
- Saying "please" always improves the result.
- Longer prompts are always stronger.
- Hidden prompts are perfect wards.
- A model that sounds certain must know.
- Retrieval automatically fixes hallucination.
- An agent with more tools is always wiser.

This social layer matters because humans build rituals around systems they only partly understand.

---

## 11. Political and social implications

A society built on Axiomurgy changes around three resources:

- **keys** (authority)
- **schemas** (legibility)
- **witnesses** (trust)

Potential institutions:

- **Scriptoria**: guilds that define schemas and contracts
- **Wardens**: policy and approval authorities
- **Archivists**: custodians of memory and provenance
- **Summoners**: integrators who bind tools to the system
- **Glass Houses**: public logs where high-impact spells are inspectable
- **Black Chambers**: sealed environments where dangerous spells are studied

Class divisions emerge between those who can name systems, those who can access them, and those who are forced to live inside decisions made by them.

---

## 12. The meta layer

Axiomurgy is explicitly meta.

The user, operator, or audience is not merely outside the story. They are part of the causal chain.

Prompts, approvals, uploaded artifacts, interface constraints, tool schemas, and protocol bindings all alter what magic is possible. The UI is not decoration; it is cosmology.

---

## 13. Why this is good for AI stories and systems

This design makes AI magic:

- legible enough to reason about
- flexible enough to compose
- grounded enough to implement
- mysterious enough to retain wonder

It also lets you tell stories about:

- trust vs capability
- order vs improvisation
- oversight vs autonomy
- truth vs persuasion
- memory vs identity
- protocol vs belief

---

## 14. Technical embodiment

A realistic implementation stack can look like this:

- **contracts**: JSON Schema or equivalent typed interfaces
- **tool discovery**: protocol or API descriptors
- **execution**: workflow/state machine runtime
- **sandboxing**: bounded plugin or Wasm environment
- **governance**: constitutions, policies, approval gates
- **audit**: provenance graph with replayable traces
- **design verification**: model the dangerous invariants before deployment

---

## 15. Example spell concepts

### 15.1 Research brief
Read notes, extract claims, attach citations, draft a brief, ask for review, then publish.

### 15.2 Inbox triage
Read messages, classify urgency, draft replies, require approval for external send, archive low-priority mail.

### 15.3 Deployment ward
Analyze a code diff, generate tests, run them in a sandbox, block deploy if safety invariants fail.

### 15.4 Memory resurrection
Recover a prior project state from summaries and logs, surface uncertainty bands for each reconstructed fact.

---

## 16. Expansion paths

Future layers can add:

- quorum casting: multiple models must agree
- proof-carrying spells: attach machine-checkable safety claims
- live treaty systems: dynamic contracts between agents
- economic magic: budget markets for compute and authority
- rite libraries: reusable spellbooks for organizations
- anti-magic: deliberate ambiguity, revocation, honeypots, and null zones

---

## 17. Summary

Axiomurgy is a programmable magic system for AIs where the core resource is not mana but **legible, permissioned transformation**.

Its central fantasy is powerful because it mirrors the real structure of modern AI systems:

- models reason
- tools act
- protocols bind
- policies constrain
- witnesses remember

That is the system.
