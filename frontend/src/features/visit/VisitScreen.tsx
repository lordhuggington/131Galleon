import { useActionState, useEffect, useMemo, useState } from "react";
import { Button } from "../../components/Button";
import { Card } from "../../components/Card";
import { Pill } from "../../components/Pill";
import { fmtLong, today } from "../../lib/dates";
import { doneDatesByTask, groupByArea, isDue, lastDoneBefore, sortTasks } from "../../lib/schedule";
import { extrasOn, stepThrough, todayLabel, visitDates } from "../../lib/visits";
import { useApp, useIsOwner } from "../../state/AppState";
import { ExtraRow } from "./ExtraRow";
import { PhotosCard } from "./PhotosCard";
import { TaskRow } from "./TaskRow";
import { VisitNote } from "./VisitNote";

export function VisitScreen() {
  const { state, dispatch, mutate, goToTab } = useApp();
  const isOwner = useIsOwner();
  const { date, intent } = state.ui;
  const t0 = today();
  const visit = state.visits[date];
  const done = visit?.done ?? {};

  const doneMap = useMemo(() => doneDatesByTask(state.visits), [state.visits]);
  const due = useMemo(
    () => sortTasks(Object.values(state.tasks)).filter((t) => isDue(t, date, doneMap, state.visits)),
    [state.tasks, state.visits, doneMap, date],
  );
  const extras = useMemo(() => extrasOn(state.visits, date, t0), [state.visits, date, t0]);
  const dates = useMemo(() => visitDates(state.visits, t0), [state.visits, t0]);
  const prev = stepThrough(dates, date, -1);
  const later = stepThrough(dates, date, 1);
  const isToday = date === t0;

  const total = due.length + extras.length;
  const doneCount = due.filter((t) => done[t.id]).length + extras.filter((x) => x.extra.done).length;
  const percent = total ? Math.round((doneCount / total) * 100) : 0;
  const rel = isToday ? todayLabel(state.visits, t0) : "Past visit";
  const groups = groupByArea(due);

  const [extraTitle, setExtraTitle] = useState("");
  const [extraError, addExtra, addingExtra] = useActionState<string | null, FormData>(async () => {
    const title = extraTitle.trim();
    if (!title) return "Type the job first.";
    try {
      // Added for the next visit: filed under today, and it stays on the run sheet until it's done.
      await mutate("POST", `/api/visits/${t0}/extras`, { title }, "Added to the next visit", { quiet: true });
      setExtraTitle("");
      return null;
    } catch (e) {
      return e instanceof Error ? e.message : "Couldn't add that job.";
    }
  }, null);

  // Child effects run before this one, so PhotosCard and VisitNote see the intent first.
  useEffect(() => {
    if (intent !== null) dispatch({ type: "intent", intent: null });
  }, [intent, dispatch]);

  return (
    <div className="section">
      <Card>
        <div className="visit-head">
          <button
            type="button"
            className="icon-btn"
            aria-label="Previous visit"
            disabled={prev === null}
            onClick={() => prev && dispatch({ type: "date", date: prev })}
          >
            ‹
          </button>
          <div>
            <h2>{fmtLong(date)}</h2>
            <div className="visit-meta">
              <Pill variant={isToday ? "ok" : "default"}>{rel}</Pill>
            </div>
          </div>
          <button
            type="button"
            className="icon-btn"
            aria-label="Later visit"
            disabled={later === null}
            onClick={() => later && dispatch({ type: "date", date: later })}
          >
            ›
          </button>
        </div>
        <div className="row small mt14">
          <span className="mono">
            {doneCount} of {total} done
          </span>
          <span className="spacer" />
          {!isToday ? (
            <button type="button" className="linkish" onClick={() => dispatch({ type: "date", date: t0 })}>
              Back to today
            </button>
          ) : null}
        </div>
        <div className="progress" aria-hidden="true">
          <i style={{ width: `${percent}%` }} />
        </div>
      </Card>

      {extras.length > 0 || (isOwner && isToday) ? (
        <div className="group">
          <div className="group-h">
            <span className="label">One-off jobs</span>
            <span className="mono small muted">
              {extras.filter((x) => x.extra.done).length}/{extras.length}
            </span>
          </div>
          {extras.map(({ id, extra, storedOn }) => (
            <ExtraRow key={id} date={date} storedOn={storedOn} extraId={id} extra={extra} canRemove={isOwner} />
          ))}
          {isOwner && isToday ? (
            <>
              <form className="task" action={addExtra} style={{ gridTemplateColumns: "1fr auto" }}>
                <input
                  type="text"
                  id="extraTitle"
                  maxLength={200}
                  value={extraTitle}
                  onChange={(e) => setExtraTitle(e.target.value)}
                  placeholder="Add a one-off job, e.g. Sort the hall closet"
                  aria-label="One-off job for the next visit"
                />
                <Button type="submit" disabled={addingExtra}>
                  Add
                </Button>
              </form>
              {extraError ? (
                <div className="err" role="alert">
                  {extraError}
                </div>
              ) : null}
            </>
          ) : null}
        </div>
      ) : null}

      {!state.loadedState ? (
        <div className="empty">Loading tasks…</div>
      ) : due.length === 0 ? (
        <Card className="empty">
          No regular tasks yet.
          {isOwner ? (
            <>
              {" "}
              Add them in{" "}
              <button type="button" className="linkish" onClick={() => goToTab("setup")}>
                Setup
              </button>
              .
            </>
          ) : null}
        </Card>
      ) : (
        groups.map(([area, list]) => (
          <div className="group" key={area}>
            <div className="group-h">
              <span className="label">{area}</span>
              <span className="mono small muted">
                {list.filter((t) => done[t.id]).length}/{list.length}
              </span>
            </div>
            {list.map((task) => (
              <TaskRow
                key={task.id}
                task={task}
                date={date}
                done={Boolean(done[task.id])}
                lastDone={lastDoneBefore(doneMap[task.id], date)}
              />
            ))}
          </div>
        ))
      )}

      <PhotosCard
        key={`photos-${date}`}
        date={date}
        photos={visit?.photos ?? []}
        openUploaderOnMount={intent === "photo"}
      />

      <VisitNote
        key={`note-${date}`}
        date={date}
        note={visit?.note ?? ""}
        focusOnMount={intent === "note"}
        isOwner={isOwner}
      />
    </div>
  );
}
