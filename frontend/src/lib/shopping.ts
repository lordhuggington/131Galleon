// Shopping list filtering, grouping and the plain-text export. Ported 1:1 from v1 static/app.js.
import type { ShoppingItem } from "../api/types";
import { fmt } from "./dates";

export const AISLES: string[] = [
  "Produce",
  "Meat",
  "Dairy & eggs",
  "Frozen",
  "Bakery",
  "Pantry",
  "Baking",
  "Spices",
];

export type ShopFilter = "all" | "tue" | "fri";

/** "tue" = deliver by Tuesday (everything except Friday-only); "fri" = Friday-only. */
export function filterItems(items: ShoppingItem[], filter: ShopFilter): ShoppingItem[] {
  return items.filter((i) => filter === "all" || (filter === "tue" ? i.for !== "fri" : i.for === "fri"));
}

export function aisleRank(aisle: string): number {
  const i = AISLES.indexOf(aisle);
  return i < 0 ? 99 : i;
}

export function groupByAisle(items: ShoppingItem[]): Array<[string, ShoppingItem[]]> {
  const groups = new Map<string, ShoppingItem[]>();
  for (const i of items) {
    const key = i.aisle || "Other";
    const list = groups.get(key);
    if (list) list.push(i);
    else groups.set(key, [i]);
  }
  return [...groups.entries()].sort((a, b) => aisleRank(a[0]) - aisleRank(b[0]));
}

/** Amazon Fresh search for one item; older plans have no phrase, so the item and quantity stand in. */
export function freshSearchUrl(item: ShoppingItem): string {
  const query = item.search.trim() || `${item.item} ${item.buy}`.trim();
  return `https://www.amazon.com/s?k=${encodeURIComponent(query)}&i=amazonfresh`;
}

export function shoppingText(
  week: string,
  items: ShoppingItem[],
  got: Record<string, boolean>,
  filter: ShopFilter,
): string {
  const remaining = filterItems(items, filter).filter((i) => !got[i.id]);
  const out: string[] = [`Groceries – week of ${fmt(week, { month: "short", day: "numeric" })}`];
  const fresh = remaining.filter((i) => !i.stock);
  // Aisle order here is first-appearance order, matching v1's export.
  const aisles = [...new Set(fresh.map((i) => i.aisle || "Other"))];
  for (const aisle of aisles) {
    out.push("", aisle.toUpperCase());
    for (const i of fresh.filter((x) => (x.aisle || "Other") === aisle)) out.push(`- ${i.item}: ${i.buy}`);
  }
  const stock = remaining.filter((i) => i.stock);
  if (stock.length) {
    out.push("", "PANTRY (if out)");
    for (const i of stock) out.push(`- ${i.item}: ${i.buy}`);
  }
  return out.join("\n");
}
