import { describe, expect, it } from "vitest";
import { compilePlan } from "./planner.js";

describe("compilePlan", () => {
  it("orders by dependencies", () => {
    const doc = {
      spell: "t",
      intent: "i",
      graph: [
        { id: "a", rune: "mirror.read", effect: "read", args: {} },
        { id: "b", rune: "x.y", requires: ["a"], args: { from: "$a" } },
      ],
    };
    const p = compilePlan(doc);
    expect(p.map((s) => s.id)).toEqual(["a", "b"]);
  });
});
