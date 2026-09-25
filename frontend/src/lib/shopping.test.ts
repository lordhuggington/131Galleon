import { describe, expect, it } from "vitest";
import type { ShoppingItem } from "../api/types";
import { AISLES, aisleRank, filterItems, groupByAisle, shoppingText } from "./shopping";

function item(over: Partial<ShoppingItem> & { id: string; item: string }): ShoppingItem {
  return {
    id: over.id,
    item: over.item,
    buy: over.buy ?? "1 pack",
    aisle: over.aisle ?? "Pantry",
    for: over.for ?? "both",
    stock: over.stock ?? false,
  };
}

const items: ShoppingItem[] = [
  item({ id: "i1", item: "Chicken breast", buy: "2 packs", aisle: "Meat", for: "both" }),
  item({ id: "i2", item: "Spinach", buy: "1 bag", aisle: "Produce", for: "tue" }),
  item({ id: "i3", item: "Olive oil", buy: "1 bottle", aisle: "Pantry", for: "both", stock: true }),
  item({ id: "i4", item: "Salmon", buy: "3 fillets", aisle: "Meat", for: "fri" }),
];

describe("AISLES / aisleRank", () => {
  it("lists aisles in shopping order", () => {
    expect(AISLES[0]).toBe("Produce");
    expect(AISLES).toContain("Spices");
  });

  it("ranks unknown aisles last", () => {
    expect(aisleRank("Produce")).toBe(0);
    expect(aisleRank("Other")).toBe(99);
    expect(aisleRank("Meat")).toBeLessThan(aisleRank("Pantry"));
  });
});

describe("filterItems", () => {
  it("keeps everything for 'all'", () => {
    expect(filterItems(items, "all").map((i) => i.id)).toEqual(["i1", "i2", "i3", "i4"]);
  });

  it("drops Friday-only items for 'tue'", () => {
    expect(filterItems(items, "tue").map((i) => i.id)).toEqual(["i1", "i2", "i3"]);
  });

  it("keeps only Friday-only items for 'fri'", () => {
    expect(filterItems(items, "fri").map((i) => i.id)).toEqual(["i4"]);
  });
});

describe("groupByAisle", () => {
  it("groups by aisle in shopping order, unknown aisles last", () => {
    const grouped = groupByAisle([
      item({ id: "a", item: "Flour", aisle: "Baking" }),
      item({ id: "b", item: "Apples", aisle: "Produce" }),
      item({ id: "c", item: "Ice", aisle: "Freezer aisle" }),
      item({ id: "d", item: "Pears", aisle: "Produce" }),
    ]);
    expect(grouped.map(([aisle, list]) => [aisle, list.map((i) => i.id)])).toEqual([
      ["Produce", ["b", "d"]],
      ["Baking", ["a"]],
      ["Freezer aisle", ["c"]],
    ]);
  });

  it("labels a blank aisle Other", () => {
    const grouped = groupByAisle([item({ id: "a", item: "Mystery", aisle: "" })]);
    expect(grouped.map(([aisle, list]) => [aisle, list.map((i) => i.id)])).toEqual([["Other", ["a"]]]);
  });
});

describe("shoppingText", () => {
  it("writes the list Owen pastes into the grocery app, skipping ticked items", () => {
    const text = shoppingText("2026-09-21", items, { i2: true }, "all");
    expect(text).toBe(
      [
        "Groceries – week of Sep 21",
        "",
        "MEAT",
        "- Chicken breast: 2 packs",
        "- Salmon: 3 fillets",
        "",
        "PANTRY (if out)",
        "- Olive oil: 1 bottle",
      ].join("\n"),
    );
  });

  it("respects the Friday-only filter", () => {
    const text = shoppingText("2026-09-21", items, {}, "fri");
    expect(text).toBe(["Groceries – week of Sep 21", "", "MEAT", "- Salmon: 3 fillets"].join("\n"));
  });

  it("returns just the header when everything is ticked off", () => {
    const text = shoppingText("2026-09-21", items, { i1: true, i2: true, i3: true, i4: true }, "all");
    expect(text).toBe("Groceries – week of Sep 21");
  });
});
