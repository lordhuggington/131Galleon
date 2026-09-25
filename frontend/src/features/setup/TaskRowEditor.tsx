import { useEffect, useState } from "react";
import type { Day, Freq, Task } from "../../api/types";
import { Button } from "../../components/Button";
import { InlineConfirm } from "../../components/InlineConfirm";
import { DAY_LABEL, FREQ_LABEL } from "../../lib/schedule";
import { useApp } from "../../state/AppState";

const FREQS = Object.keys(FREQ_LABEL) as Freq[];
const DAYS = Object.keys(DAY_LABEL) as Day[];

export function TaskRowEditor({ task }: { task: Task }) {
  const { state, dispatch, mutate } = useApp();
  const [title, setTitle] = useState(task.title);
  const [area, setArea] = useState(task.area);
  const [notes, setNotes] = useState(task.notes);
  const [focused, setFocused] = useState<"title" | "area" | "notes" | null>(null);

  // Adopt server changes only for fields this row isn't mid-edit on (spec §7.2).
  useEffect(() => {
    if (focused !== "title") setTitle(task.title);
  }, [task.title, focused]);
  useEffect(() => {
    if (focused !== "area") setArea(task.area);
  }, [task.area, focused]);
  useEffect(() => {
    if (focused !== "notes") setNotes(task.notes);
  }, [task.notes, focused]);

  function patch(fields: Partial<Task>) {
    dispatch({ type: "patch-task", taskId: task.id, fields });
    void mutate("PATCH", `/api/tasks/${encodeURIComponent(task.id)}`, fields, "Saved").catch(() => {});
  }

  function commitTitle() {
    const value = title.trim();
    if (!value) {
      setTitle(task.title);
      return;
    }
    if (value !== task.title) patch({ title: value });
  }

  function commitArea() {
    const value = area.trim();
    if (value !== task.area) patch({ area: value });
  }

  function commitNotes() {
    const value = notes.trim();
    if (value !== task.notes) patch({ notes: value });
  }

  function remove() {
    dispatch({ type: "delete-task", taskId: task.id });
    void mutate("DELETE", `/api/tasks/${encodeURIComponent(task.id)}`, undefined, "Task deleted").catch(() => {});
  }

  return (
    <div className="trow">
      <input
        type="text"
        className="title-in"
        maxLength={200}
        aria-label="Task"
        value={title}
        onChange={(e) => setTitle(e.target.value)}
        onFocus={() => setFocused("title")}
        onBlur={() => {
          setFocused(null);
          commitTitle();
        }}
      />
      <input
        type="text"
        maxLength={60}
        list="areaList"
        aria-label="Area"
        value={area}
        onChange={(e) => setArea(e.target.value)}
        onFocus={() => setFocused("area")}
        onBlur={() => {
          setFocused(null);
          commitArea();
        }}
      />
      <select aria-label="How often" value={task.freq} onChange={(e) => patch({ freq: e.target.value as Freq })}>
        {FREQS.map((freq) => (
          <option key={freq} value={freq}>
            {FREQ_LABEL[freq]}
          </option>
        ))}
      </select>
      <select aria-label="Which day" value={task.day} onChange={(e) => patch({ day: e.target.value as Day })}>
        {DAYS.map((day) => (
          <option key={day} value={day}>
            {day === "any" ? "Either day" : DAY_LABEL[day]}
          </option>
        ))}
      </select>
      {state.ui.confirm === `del:${task.id}` ? (
        <InlineConfirm
          confirmLabel="Delete"
          onConfirm={remove}
          onCancel={() => dispatch({ type: "confirm", key: null })}
        />
      ) : (
        <Button variant="ghost" aria-label="Delete task" onClick={() => dispatch({ type: "confirm", key: `del:${task.id}` })}>
          Delete
        </Button>
      )}
      <input
        type="text"
        className="notes"
        maxLength={500}
        aria-label="Notes"
        placeholder="Notes for the housekeeper (optional)"
        value={notes}
        onChange={(e) => setNotes(e.target.value)}
        onFocus={() => setFocused("notes")}
        onBlur={() => {
          setFocused(null);
          commitNotes();
        }}
      />
    </div>
  );
}
