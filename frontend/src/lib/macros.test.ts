import { describe, expect, it } from "vitest";
import type { Ingredient, Recipe, Settings } from "../api/types";
import { macroStatus, macros } from "./macros";

function recipe(portions: number, ingredients: Ingredient[]): Recipe {
  return {
    title: "Chicken traybake",
    blurb: "",
    portions,
    portionNote: "",
    ingredients,
    steps: [],
    storage: "",
    fav: false,
  };
}

const settings: Settings = {
  kcal: 500,
  protein: 50,
  tue: { breakfast: 3, main: 9, dessert: 3, covers: "Wed, Thu, Fri" },
  fri: { breakfast: 4, main: 12, dessert: 4, covers: "Sat, Sun, Mon, Tue" },
  store: "",
  likes: "",
  dislikes: "",
  pantry: "",
};

describe("macros", () => {
  it("divides ingredient totals by portions and rounds", () => {
    const r = recipe(3, [
      { item: "Chicken", amount: "1 kg", kcal: 1000, protein: 120 },
      { item: "Rice", amount: "500 g", kcal: 500, protein: 30 },
    ]);
    expect(macros(r)).toEqual({ kcal: 500, protein: 50 });
  });

  it("treats a zero portion count as one", () => {
    const r = recipe(0, [{ item: "Oats", amount: "1 kg", kcal: 420, protein: 44 }]);
    expect(macros(r)).toEqual({ kcal: 420, protein: 44 });
  });

  it("returns zeroes for a recipe with no ingredients", () => {
    expect(macros(recipe(4, []))).toEqual({ kcal: 0, protein: 0 });
  });

  it("ignores ingredients whose macros are missing from the server payload", () => {
    const ingredients = [
      { item: "Chicken", amount: "1 kg", kcal: 900, protein: 100 },
      { item: "Salt", amount: "a pinch" } as unknown as Ingredient,
    ];
    expect(macros(recipe(2, ingredients))).toEqual({ kcal: 450, protein: 50 });
  });
});

describe("macroStatus", () => {
  it("is on target when calories are within 7% and protein is no more than 3 g under", () => {
    const r = recipe(1, [{ item: "x", amount: "", kcal: 520, protein: 48 }]);
    expect(macroStatus(r, settings)).toEqual({ kcal: 520, protein: 48, ok: true });
  });

  it("accepts exactly 7% off", () => {
    const r = recipe(1, [{ item: "x", amount: "", kcal: 535, protein: 50 }]);
    expect(macroStatus(r, settings).ok).toBe(true);
  });

  it("is off target when calories miss by more than 7%", () => {
    const r = recipe(1, [{ item: "x", amount: "", kcal: 560, protein: 60 }]);
    expect(macroStatus(r, settings).ok).toBe(false);
  });

  it("is off target when protein is more than 3 g under", () => {
    const r = recipe(1, [{ item: "x", amount: "", kcal: 500, protein: 46 }]);
    expect(macroStatus(r, settings).ok).toBe(false);
  });
});
