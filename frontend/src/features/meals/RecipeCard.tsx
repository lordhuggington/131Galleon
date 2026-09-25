import { startTransition, useOptimistic } from "react";
import type { Recipe, SessionKey, Slot } from "../../api/types";
import { Pill } from "../../components/Pill";
import { macroStatus } from "../../lib/macros";
import { useApp, useSettings } from "../../state/AppState";

export function RecipeCard({
  week,
  session,
  slot,
  label,
  recipe,
  canFavourite,
}: {
  week: string;
  session: SessionKey;
  slot: Slot;
  label: string;
  recipe: Recipe;
  canFavourite: boolean;
}) {
  const { mutate } = useApp();
  const settings = useSettings();
  // The star has no shared counter, so an optimistic row value is enough here.
  const [fav, setFav] = useOptimistic(recipe.fav);
  const status = macroStatus(recipe, settings);

  function toggleFav() {
    const next = !recipe.fav;
    startTransition(async () => {
      setFav(next);
      try {
        await mutate("PATCH", `/api/plans/${week}/recipes/${session}/${slot}`, { fav: next });
      } catch {
        // mutate() has toasted the error and resynced; the optimistic value falls back.
      }
    });
  }

  return (
    <article className="recipe">
      <div className="row">
        <span className="label">{label}</span>
        <span className="spacer" />
        <Pill className="mono">× {recipe.portions} portions</Pill>
        {canFavourite ? (
          <button type="button" className="star" aria-pressed={fav} onClick={toggleFav}>
            {fav ? "★ Favourite" : "☆ Favourite"}
          </button>
        ) : null}
      </div>
      <h3>{recipe.title}</h3>
      {recipe.blurb ? <div className="blurb">{recipe.blurb}</div> : null}
      <div className="macros">
        <Pill variant={status.ok ? "ok" : "warn"}>{status.kcal} kcal</Pill>
        <Pill variant={status.ok ? "ok" : "warn"}>{status.protein} g protein</Pill>
        <Pill>per portion</Pill>
        {status.ok ? null : <Pill variant="warn">off target</Pill>}
      </div>
      <div className="recipe-body">
        <div>
          <div className="label mb4">Ingredients for the whole batch</div>
          <table className="ing">
            <tbody>
              {recipe.ingredients.map((ingredient, index) => (
                <tr key={`${index}-${ingredient.item}`}>
                  <td>{ingredient.item}</td>
                  <td>{ingredient.amount}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <div>
          <div className="label mb6">Method</div>
          <ol className="steps">
            {recipe.steps.map((step, index) => (
              <li key={`${index}-${step.slice(0, 16)}`}>{step}</li>
            ))}
          </ol>
          {recipe.portionNote ? (
            <div className="store">
              <b>Portions:</b> {recipe.portionNote}
            </div>
          ) : null}
          {recipe.storage ? (
            <div className="store mt8">
              <b>Storage:</b> {recipe.storage}
            </div>
          ) : null}
        </div>
      </div>
    </article>
  );
}
