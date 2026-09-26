// Per-portion macros and the "on target" rule. Ported 1:1 from v1 static/app.js.
import type { Recipe, Settings } from "../api/types";

export interface Macros {
  kcal: number;
  protein: number;
}

export interface MacroStatus extends Macros {
  ok: boolean;
}

export function macros(r: Recipe): Macros {
  const n = Math.max(1, Number(r.portions) || 1);
  const list = r.ingredients ?? [];
  const kcal = list.reduce((sum, i) => sum + (Number(i.kcal) || 0), 0) / n;
  const protein = list.reduce((sum, i) => sum + (Number(i.protein) || 0), 0) / n;
  return { kcal: Math.round(kcal), protein: Math.round(protein) };
}

export function macroStatus(r: Recipe, s: Settings): MacroStatus {
  const m = macros(r);
  const ok = Math.abs(m.kcal - s.kcal) <= s.kcal * 0.07 && m.protein >= s.protein - 3;
  return { ...m, ok };
}
