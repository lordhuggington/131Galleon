import { describe, expect, it } from "vitest";
import type { ShoppingItem } from "../api/types";
import { AISLES, aisleRank, filterItems, freshSearchUrl, groupByAisle, shoppingText } from "./shopping";

function item(over: Partial<ShoppingItem> & { id: string; item: string }): ShoppingItem {
  return {
    id: over.id,
    item: over.item,
    buy: over.buy ?? "1 pack",
    aisle: over.aisle ?? "Pantry",
    for: over.for ?? "both",
    stock: over.stock ?? false,
    search: over.search ?? "",
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

describe("freshSearchUrl", () => {
  it("searches Amazon Fresh for the wording the menu asked for", () => {
    const url = freshSearchUrl(
      item({ id: "a", item: "Fage Total 0% Greek Yogurt, 32 oz", buy: "2", search: "Fage Total 0% Greek Yogurt 32 oz" }),
    );
    expect(url).toBe("https://www.amazon.com/s?k=Fage%20Total%200%25%20Greek%20Yogurt%2032%20oz&i=amazonfresh");
  });

  it("falls back to the item and the quantity when there is no phrase", () => {
    // Plans written before the search field, and anything the model left blank.
    expect(freshSearchUrl(item({ id: "b", item: "Spinach", buy: "1 bag", search: "" }))).toBe(
      "https://www.amazon.com/s?k=Spinach%201%20bag&i=amazonfresh",
    );
    expect(freshSearchUrl(item({ id: "c", item: "Garlic", buy: "", search: "   " }))).toBe(
      "https://www.amazon.com/s?k=Garlic&i=amazonfresh",
    );
  });

  it("encodes characters that would otherwise break the query string", () => {
    expect(
      freshSearchUrl(item({ id: "d", item: "Ground beef", search: "Amazon Grocery 93/7 Ground Beef & Chuck 100%" })),
    ).toBe("https://www.amazon.com/s?k=Amazon%20Grocery%2093%2F7%20Ground%20Beef%20%26%20Chuck%20100%25&i=amazonfresh");
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
