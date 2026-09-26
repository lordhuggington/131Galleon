import { useState } from "react";
import { Button } from "../../components/Button";
import { Card } from "../../components/Card";
import { Field } from "../../components/Field";
import { useApp } from "../../state/AppState";
import type { GenState } from "./useGeneration";

export function GenPanel({
  hasPlan,
  gen,
  start,
  stop,
}: {
  hasPlan: boolean;
  gen: GenState;
  start: (note: string) => void;
  stop: () => void;
}) {
  const { state, dispatch } = useApp();
  const [note, setNote] = useState("");
  const confirming = state.ui.confirm === "regen";

  return (
    <Card>
      <h3 className="h19">{hasPlan ? "Replace this week's menu" : "Create this week's menu"}</h3>
      <p className="small muted mt6">
        Claude writes the oats, main and dessert for both prep days plus one combined shopping list, sharing
        ingredients between Tuesday and Friday to cut waste. It uses your Setup preferences, last week's leftovers and
        avoids recent repeats. Takes about a minute.
      </p>
      <Field label="Anything for this week? (optional)" htmlFor="genNote">
        <input
          id="genNote"
          type="text"
          maxLength={500}
          value={note}
          disabled={gen.running}
          onChange={(e) => setNote(e.target.value)}
          placeholder="e.g. Asian flavours, no pork, use up the Parmesan"
        />
      </Field>
      <div className="row mt12">
        {gen.running ? (
          <Button onClick={stop}>Stop</Button>
        ) : hasPlan && !confirming ? (
          <Button onClick={() => dispatch({ type: "confirm", key: "regen" })}>Replace menu…</Button>
        ) : hasPlan && confirming ? (
          <>
            <span className="small">This overwrites the current menu and shopping list.</span>
            <Button variant="danger" onClick={() => start(note)}>
              Replace it
            </Button>
            <Button variant="ghost" onClick={() => dispatch({ type: "confirm", key: null })}>
              Cancel
            </Button>
          </>
        ) : (
          <Button variant="primary" onClick={() => start(note)}>
            Create menu &amp; shopping list
          </Button>
        )}
      </div>
      {gen.status || gen.error ? (
        <div className="gen-status mt10">
          {gen.error ? (
            <div className="notice warn" role="alert">{gen.error}</div>
          ) : (
            <>
              <div className={gen.running ? "muted" : ""}>{gen.status}</div>
              {gen.titles.length > 0 ? (
                <ul className="gen-titles">
                  {gen.titles.map((title, index) => (
                    <li key={index}>{title}</li>
                  ))}
                </ul>
              ) : null}
            </>
          )}
        </div>
      ) : null}
    </Card>
  );
}
