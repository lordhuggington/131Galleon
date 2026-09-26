import type { SessionKey, Slot } from "../../api/types";
import { Card } from "../../components/Card";
import { Chips } from "../../components/Chips";
import { addDays, fmtLong } from "../../lib/dates";
import { useApp, useIsOwner, useSettings } from "../../state/AppState";
import { GenPanel } from "./GenPanel";
import { RecipeCard } from "./RecipeCard";
import { useGeneration } from "./useGeneration";
import { WeekNav } from "./WeekNav";

const SLOT_LABEL: Record<Slot, string> = {
  breakfast: "Breakfast · overnight oats",
  main: "Main · 3 a day",
  dessert: "Dessert",
};
const SLOTS: Slot[] = ["breakfast", "main", "dessert"];
const SESSIONS: SessionKey[] = ["tue", "fri"];

export function MealsScreen() {
  const { state, dispatch } = useApp();
  const settings = useSettings();
  const isOwner = useIsOwner();
  // Lives here, not in GenPanel: the panel unmounts when a week change clears `loaded`, and a job in
  // flight must not die with it. This screen stays mounted for the app's lifetime under Activity.
  const { gen, start, stop } = useGeneration();
  const week = state.ui.week;
  const session = state.ui.session;
  const plan = state.plans[week] ?? null;
  const loaded = state.planLoaded[week] === true;
  const sessionPlan = plan?.sessions[session];

  return (
    <div className="section">
      <WeekNav />

      {!loaded ? (
        <div className="empty">Loading menu…</div>
      ) : !plan ? (
        <Card className="empty">
          No menu for this week yet.{isOwner ? "" : " Owen will add one before the visit."}
        </Card>
      ) : (
        <>
          <Chips<SessionKey>
            label="Prep session"
            value={session}
            onChange={(next) => dispatch({ type: "session", session: next })}
            options={SESSIONS.map((key) => ({
              value: key,
              label: `${key === "tue" ? "Tuesday prep" : "Friday prep"} · covers ${
                plan.sessions[key]?.covers ?? settings[key].covers
              }`,
            }))}
          />
          {sessionPlan ? (
            <>
              <Card>
                <div className="label">
                  Prep order for {fmtLong(sessionPlan.date || addDays(week, session === "tue" ? 1 : 4))}
                </div>
                <ol className="timeline">
                  {sessionPlan.timeline.map((step, index) => (
                    <li key={`${index}-${step.slice(0, 16)}`}>{step}</li>
                  ))}
                </ol>
              </Card>
              {SLOTS.map((slot) => {
                const recipe = sessionPlan.recipes[slot];
                return recipe ? (
                  <RecipeCard
                    key={slot}
                    week={week}
                    session={session}
                    slot={slot}
                    label={SLOT_LABEL[slot]}
                    recipe={recipe}
                    canFavourite={isOwner}
                  />
                ) : null;
              })}
            </>
          ) : (
            <div className="empty">This session is missing from the menu.</div>
          )}
        </>
      )}

      {isOwner && loaded ? <GenPanel hasPlan={plan !== null} gen={gen} start={start} stop={stop} /> : null}
    </div>
  );
}
