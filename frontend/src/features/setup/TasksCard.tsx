import { useActionState, useState } from "react";
import type { Freq } from "../../api/types";
import { Button } from "../../components/Button";
import { Card } from "../../components/Card";
import { Field } from "../../components/Field";
import { AREA_ORDER, FREQ_LABEL, groupByArea, sortTasks } from "../../lib/schedule";
import { useApp } from "../../state/AppState";
import { TaskRowEditor } from "./TaskRowEditor";

const FREQS = Object.keys(FREQ_LABEL) as Freq[];

export function TasksCard() {
  const { state, mutate } = useApp();
  const tasks = sortTasks(Object.values(state.tasks));
  const areas = [...new Set([...AREA_ORDER, ...tasks.map((t) => t.area).filter(Boolean)])];
  const groups = groupByArea(tasks);

  const [title, setTitle] = useState("");
  const [area, setArea] = useState("");
  const [freq, setFreq] = useState<Freq>("visit");

  const [error, addTask, adding] = useActionState<string | null, FormData>(async () => {
    const value = title.trim();
    if (!value) return "Type the task first.";
    try {
      // quiet: the failure is shown inline under the form.
      await mutate(
        "POST",
        "/api/tasks",
        { title: value, area: area.trim() || "Whole house", freq },
        "Task added",
        { quiet: true },
      );
      setTitle("");
      setArea("");
      setFreq("visit");
      return null;
    } catch (e) {
      return e instanceof Error ? e.message : "Couldn't add that task.";
    }
  }, null);

  return (
    <>
      <Card>
        <h3 className="h19">Regular tasks</h3>
        <p className="small muted mt6">
          Every-visit tasks show on every visit, whatever day it is. Weekly, every-2-weeks and monthly tasks show when
          they're due and stay on the list until ticked. One-off jobs are added for the next visit on the Visit tab
          and stay on it until they're done.
        </p>
        <form className="grid2 mt12" action={addTask}>
          <Field label="New task" htmlFor="newTitle">
            <input
              id="newTitle"
              type="text"
              maxLength={200}
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              placeholder="e.g. Clean the garage fridge"
            />
          </Field>
          <Field label="Area" htmlFor="newArea">
            <input
              id="newArea"
              type="text"
              maxLength={60}
              list="areaList"
              value={area}
              onChange={(e) => setArea(e.target.value)}
              placeholder="Kitchen"
            />
          </Field>
          <Field label="How often" htmlFor="newFreq">
            <select id="newFreq" value={freq} onChange={(e) => setFreq(e.target.value as Freq)}>
              {FREQS.map((value) => (
                <option key={value} value={value}>
                  {FREQ_LABEL[value]}
                </option>
              ))}
            </select>
          </Field>
          <Button type="submit" variant="primary" disabled={adding}>
            Add task
          </Button>
          {error ? (
            <div className="err" role="alert">
              {error}
            </div>
          ) : null}
        </form>
        <datalist id="areaList">
          {areas.map((value) => (
            <option key={value} value={value} />
          ))}
        </datalist>
      </Card>

      {groups.length === 0 ? (
        <Card className="empty">No tasks yet.</Card>
      ) : (
        groups.map(([groupArea, list]) => (
          <div className="group" key={groupArea}>
            <div className="group-h">
              <span className="label">{groupArea}</span>
              <span className="mono small muted">{list.length}</span>
            </div>
            {list.map((task) => (
              <TaskRowEditor key={task.id} task={task} />
            ))}
          </div>
        ))
      )}
    </>
  );
}
