import type { Task } from "../../api/types";
import { Check } from "../../components/Check";
import { Pill } from "../../components/Pill";
import { fmtDay } from "../../lib/dates";
import { DAY_LABEL, FREQ_LABEL } from "../../lib/schedule";
import { useApp } from "../../state/AppState";

export function TaskRow({
  task,
  date,
  done,
  lastDone,
  showMealsLink,
  onOpenMeals,
}: {
  task: Task;
  date: string;
  done: boolean;
  lastDone: string | null;
  showMealsLink: boolean;
  onOpenMeals: () => void;
}) {
  const { dispatch, mutate } = useApp();

  function toggle() {
    const next = !done;
    dispatch({ type: "toggle-task", date, taskId: task.id, done: next });
    void mutate("PUT", `/api/visits/${date}/tasks/${encodeURIComponent(task.id)}`, { done: next }).catch(() => {});
  }

  const recurring = task.freq !== "visit";
  const pinned = task.day !== "any";
  // v1 rule: a weekly task pinned to a day never shows the "last done" pill.
  const showLast = recurring && (task.freq !== "weekly" || !pinned);

  return (
    <div className={done ? "task done" : "task"}>
      <Check checked={done} label={task.title} onClick={toggle} />
      <div>
        <div className="t">{task.title}</div>
        {task.notes ? <div className="n">{task.notes}</div> : null}
        {task.link === "meals" && showMealsLink ? (
          <div>
            <button type="button" className="linkish" onClick={onOpenMeals}>
              Open this visit's recipes
            </button>
          </div>
        ) : null}
      </div>
      <div className="side">
        {recurring ? (
          <Pill variant="oat">
            {FREQ_LABEL[task.freq]}
            {pinned ? ` · ${DAY_LABEL[task.day]}` : ""}
          </Pill>
        ) : null}
        {showLast ? <Pill>{lastDone ? `last ${fmtDay(lastDone)}` : "first time"}</Pill> : null}
      </div>
    </div>
  );
}
