import type { Extra } from "../../api/types";
import { Check } from "../../components/Check";
import { Pill } from "../../components/Pill";
import { rowTap } from "../../lib/rowTap";
import { useApp } from "../../state/AppState";

export function ExtraRow({
  date,
  extraId,
  extra,
  canRemove,
}: {
  date: string;
  extraId: string;
  extra: Extra;
  canRemove: boolean;
}) {
  const { dispatch, mutate } = useApp();

  function toggle() {
    const next = !extra.done;
    dispatch({ type: "toggle-extra", date, extraId, done: next });
    void mutate("PATCH", `/api/visits/${date}/extras/${encodeURIComponent(extraId)}`, { done: next }).catch(() => {});
  }

  function remove() {
    dispatch({ type: "remove-extra", date, extraId });
    void mutate("DELETE", `/api/visits/${date}/extras/${encodeURIComponent(extraId)}`).catch(() => {});
  }

  return (
    <div className={extra.done ? "task done" : "task"} onClick={rowTap(toggle)}>
      <Check checked={extra.done} label={extra.title} onClick={toggle} />
      <div>
        <div className="t">{extra.title}</div>
        {extra.notes ? <div className="n">{extra.notes}</div> : null}
      </div>
      <div className="side">
        <Pill variant="oat">Just this visit</Pill>
        {canRemove ? (
          <button type="button" className="linkish" onClick={remove}>
            Remove
          </button>
        ) : null}
      </div>
    </div>
  );
}
