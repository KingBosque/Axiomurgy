/**
 * Mirrors axiomurgy/planning.compile_plan for spell JSON (topological order).
 */

export type SpellStepJson = {
  id: string;
  rune: string;
  effect?: string;
  args?: Record<string, unknown>;
  requires?: string[];
};

export type SpellDocumentJson = {
  spell: string;
  intent: string;
  inputs?: Record<string, unknown>;
  constraints?: Record<string, unknown>;
  graph: SpellStepJson[];
};

function extractReferences(value: unknown): Set<string> {
  const refs = new Set<string>();
  if (typeof value === "string") {
    if (value.startsWith("$")) refs.add(value.slice(1).split(".")[0] ?? "");
  } else if (Array.isArray(value)) {
    for (const item of value) extractReferences(item).forEach((r) => refs.add(r));
  } else if (value !== null && typeof value === "object") {
    for (const item of Object.values(value)) extractReferences(item).forEach((r) => refs.add(r));
  }
  return refs;
}

export function compilePlan(doc: SpellDocumentJson): SpellStepJson[] {
  const graph = doc.graph;
  const stepMap = new Map<string, SpellStepJson>();
  for (const s of graph) {
    if (stepMap.has(s.id)) throw new Error("Duplicate step ids in graph");
    stepMap.set(s.id, s);
  }
  const order = new Map<string, number>();
  graph.forEach((s, i) => order.set(s.id, i));

  const deps = new Map<string, Set<string>>();
  const rev = new Map<string, Set<string>>();
  for (const s of graph) {
    const need = new Set<string>(s.requires ?? []);
    for (const ref of extractReferences(s.args ?? {})) {
      if (ref !== "inputs") need.add(ref);
    }
    for (const dep of need) {
      if (!stepMap.has(dep)) throw new Error(`Step '${s.id}' depends on unknown steps: ${dep}`);
    }
    deps.set(s.id, need);
    for (const dep of need) {
      if (!rev.has(dep)) rev.set(dep, new Set());
      rev.get(dep)!.add(s.id);
    }
  }

  const depsCopy = new Map<string, Set<string>>();
  for (const s of graph) depsCopy.set(s.id, new Set(deps.get(s.id)));

  const out: string[] = [];
  const inOut = new Set<string>();

  while (out.length < graph.length) {
    const ready = graph
      .filter((s) => !inOut.has(s.id) && depsCopy.get(s.id)!.size === 0)
      .sort((a, b) => order.get(a.id)! - order.get(b.id)!);
    if (ready.length === 0) throw new Error("Cycle detected in spell graph");
    const current = ready[0]!.id;
    inOut.add(current);
    out.push(current);
    const children = [...(rev.get(current) ?? [])].sort((a, b) => order.get(a)! - order.get(b)!);
    for (const child of children) {
      depsCopy.get(child)!.delete(current);
    }
  }

  return out.map((id) => stepMap.get(id)!);
}
