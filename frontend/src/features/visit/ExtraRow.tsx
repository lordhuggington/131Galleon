import type { Extra } from "../../api/types";
import { Check } from "../../components/Check";
import { Pill } from "../../components/Pill";
import { rowTap } from "../../lib/rowTap";
import { useApp } from "../../state/AppState";

export function ExtraRow({
  date,
  storedOn,
  extraId,
  extra,
  canRemove,
}: {
  /** The run sheet's date: ticking files the extra under it. */
  date: string;
  /** Where the extra is stored now (see lib/visits). */
  storedOn: string;
  extraId: string;
  extra: Extra;
  canRemove: boolean;
}) {
  const { dispatch, mutate } = useApp();

  function toggle() {
    const next = !extra.done;
    dispatch({ type: "toggle-extra", storedOn, date, extraId, done: next });
    void mutate("PATCH", `/api/visits/${date}/extras/${encodeURIComponent(extraId)}`, { done: next }).catch(() => {});
  }

  function remove() {
    dispatch({ type: "remove-extra", storedOn, extraId });
    void mutate("DELETE", `/api/visits/${storedOn}/extras/${encodeURIComponent(extraId)}`).catch(() => {});
  }

  return (
    <div className={extra.done ? "task done" : "task"} onClick={rowTap(toggle)}>
      <Check checked={extra.done} label={extra.title} onClick={toggle} />
      <div>
        <div className="t">{extra.title}</div>
        {extra.notes ? <div className="n">{extra.notes}</div> : null}
      </div>
      <div className="side">
        <Pill variant="oat">One-off</Pill>
        {canRemove ? (
          <button type="button" className="linkish" onClick={remove}>
            Remove
          </button>
        ) : null}
      </div>
    </div>
  );
}
