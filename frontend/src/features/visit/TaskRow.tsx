import type { Task } from "../../api/types";
import { Check } from "../../components/Check";
import { Pill } from "../../components/Pill";
import { fmtDay } from "../../lib/dates";
import { rowTap } from "../../lib/rowTap";
import { FREQ_LABEL } from "../../lib/schedule";
import { useApp } from "../../state/AppState";

export function TaskRow({
  task,
  date,
  done,
  lastDone,
}: {
  task: Task;
  date: string;
  done: boolean;
  lastDone: string | null;
}) {
  const { dispatch, mutate } = useApp();

  function toggle() {
    const next = !done;
    dispatch({ type: "toggle-task", date, taskId: task.id, done: next });
    void mutate("PUT", `/api/visits/${date}/tasks/${encodeURIComponent(task.id)}`, { done: next }).catch(() => {});
  }

  const recurring = task.freq !== "visit";

  return (
    <div className={done ? "task done" : "task"} onClick={rowTap(toggle)}>
      <Check checked={done} label={task.title} onClick={toggle} />
      <div>
        <div className="t">{task.title}</div>
        {task.notes ? <div className="n">{task.notes}</div> : null}
      </div>
      <div className="side">
        {recurring ? (
<Pill variant="oat">{FREQ_LABEL[task.freq]}</Pill>
        ) : null}
        {recurring ? <Pill>{lastDone ? `last ${fmtDay(lastDone)}` : "first time"}</Pill> : null}
      </div>
    </div>
  );
}
