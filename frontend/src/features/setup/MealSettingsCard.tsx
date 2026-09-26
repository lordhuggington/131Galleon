import { useActionState, useState } from "react";
import type { SessionKey, Settings } from "../../api/types";
import { Button } from "../../components/Button";
import { Card } from "../../components/Card";
import { Field } from "../../components/Field";
import { useApp, useSettings } from "../../state/AppState";

const SESSIONS: SessionKey[] = ["tue", "fri"];

export function MealSettingsCard() {
  const { mutate } = useApp();
  const saved = useSettings();
  const [form, setForm] = useState<Settings>(saved);
  const [dirty, setDirty] = useState(false);
  // Adopt server changes while nothing has been typed into this form.
  if (!dirty && form !== saved) setForm(saved);

  function change(patch: Partial<Settings>) {
    setDirty(true);
    setForm((current) => ({ ...current, ...patch }));
  }

  function changeSession(key: SessionKey, patch: Partial<Settings["tue"]>) {
    setDirty(true);
    setForm((current) => ({ ...current, [key]: { ...current[key], ...patch } }));
  }

  const [error, save, saving] = useActionState<string | null, FormData>(async () => {
    try {
      // quiet: the failure is shown inline above the button.
      await mutate("PUT", "/api/settings", form, "Settings saved", { quiet: true });
      setDirty(false);
      return null;
    } catch (e) {
      return e instanceof Error ? e.message : "Couldn't save those settings.";
    }
  }, null);

  return (
    <Card>
      <form action={save} className="section">
        <h3 className="h19">Meal plan settings</h3>
        <div className="grid2">
          <Field label="Calories per portion" htmlFor="sKcal">
            <input
              id="sKcal"
              type="number"
              min={200}
              max={1500}
              value={form.kcal}
              onChange={(e) => change({ kcal: Number(e.target.value) })}
            />
          </Field>
          <Field label="Protein per portion (g)" htmlFor="sProt">
            <input
              id="sProt"
              type="number"
              min={10}
              max={150}
              value={form.protein}
              onChange={(e) => change({ protein: Number(e.target.value) })}
            />
          </Field>
        </div>
        {SESSIONS.map((key) => (
          <div key={key}>
            <div className="label mb6">{key === "tue" ? "Tuesday" : "Friday"} prep: portions</div>
            <div className="grid3">
              <Field label="Oats" htmlFor={`s-${key}-b`}>
                <input
                  id={`s-${key}-b`}
                  type="number"
                  min={0}
                  max={30}
                  value={form[key].breakfast}
                  onChange={(e) => changeSession(key, { breakfast: Number(e.target.value) })}
                />
              </Field>
              <Field label="Main" htmlFor={`s-${key}-m`}>
                <input
                  id={`s-${key}-m`}
                  type="number"
                  min={0}
                  max={40}
                  value={form[key].main}
                  onChange={(e) => changeSession(key, { main: Number(e.target.value) })}
                />
              </Field>
              <Field label="Dessert" htmlFor={`s-${key}-d`}>
                <input
                  id={`s-${key}-d`}
                  type="number"
                  min={0}
                  max={30}
                  value={form[key].dessert}
                  onChange={(e) => changeSession(key, { dessert: Number(e.target.value) })}
                />
              </Field>
            </div>
            <div className="mt8">
              <Field label="Days it covers" htmlFor={`s-${key}-c`}>
                <input
                  id={`s-${key}-c`}
                  type="text"
                  maxLength={80}
                  value={form[key].covers}
                  onChange={(e) => changeSession(key, { covers: e.target.value })}
                />
              </Field>
            </div>
          </div>
        ))}
        <Field label="Where you order from" htmlFor="sStore">
          <input
            id="sStore"
            type="text"
            maxLength={1000}
            value={form.store}
            onChange={(e) => change({ store: e.target.value })}
            placeholder="e.g. Whole Foods via Amazon, Ralphs on Instacart"
          />
        </Field>
        <Field label="Flavours and meals you like" htmlFor="sLikes">
          <textarea
            id="sLikes"
            maxLength={1000}
            value={form.likes}
            onChange={(e) => change({ likes: e.target.value })}
            placeholder="e.g. Mexican, Thai curries, pesto pasta, anything with peanut butter"
          />
        </Field>
        <Field label="Never use" htmlFor="sDislikes">
          <textarea
            id="sDislikes"
            maxLength={1000}
            value={form.dislikes}
            onChange={(e) => change({ dislikes: e.target.value })}
            placeholder="e.g. mushrooms, tofu, olives"
          />
        </Field>
        <Field label="Always in the pantry (left off shopping lists)" htmlFor="sPantry">
          <textarea
            id="sPantry"
            maxLength={1000}
            value={form.pantry}
            onChange={(e) => change({ pantry: e.target.value })}
            placeholder="e.g. olive oil, salt, pepper, cumin, vanilla whey"
          />
        </Field>
        {error ? (
          <div className="err" role="alert">
            {error}
          </div>
        ) : null}
        <div>
          <Button type="submit" variant="primary" disabled={saving}>
            Save settings
          </Button>
        </div>
      </form>
    </Card>
  );
}
