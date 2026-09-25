import { useActionState, useEffect, useMemo, useState } from "react";
import { Button } from "../../components/Button";
import { Card } from "../../components/Card";
import { Pill } from "../../components/Pill";
import { fmtLong, fmtShort, mondayOf, nextVisit, sessionOf, stepVisit, today } from "../../lib/dates";
import { doneDatesByTask, groupByArea, isDue, lastDoneBefore, sortTasks } from "../../lib/schedule";
import { useApp, useCanSeeMeals, useIsOwner, useSettings } from "../../state/AppState";
import { ExtraRow } from "./ExtraRow";
import { PhotosCard } from "./PhotosCard";
import { TaskRow } from "./TaskRow";
import { VisitNote } from "./VisitNote";

export function VisitScreen() {
  const { state, dispatch, mutate, goToTab, showWeek } = useApp();
  const settings = useSettings();
  const isOwner = useIsOwner();
  const canSeeMeals = useCanSeeMeals();
  const { date, intent } = state.ui;
  const t0 = today();
  const next = nextVisit(t0);
  const visit = state.visits[date];
  const done = visit?.done ?? {};

  const doneMap = useMemo(() => doneDatesByTask(state.visits), [state.visits]);
  const due = useMemo(
    () => sortTasks(Object.values(state.tasks)).filter((t) => isDue(t, date, doneMap, state.visits)),
    [state.tasks, state.visits, doneMap, date],
  );
  const extras = useMemo(
    () => Object.entries(visit?.extras ?? {}).sort((a, b) => a[1].createdAt.localeCompare(b[1].createdAt)),
    [visit],
  );

  const total = due.length + extras.length;
  const doneCount = due.filter((t) => done[t.id]).length + extras.filter(([, x]) => x.done).length;
  const percent = total ? Math.round((doneCount / total) * 100) : 0;
  const rel = date === t0 ? "Today" : date === next ? "Next visit" : date < t0 ? "Past visit" : "Coming up";
  const session = sessionOf(date);
  const groups = groupByArea(due);

  const [extraTitle, setExtraTitle] = useState("");
  const [extraError, addExtra, addingExtra] = useActionState<string | null, FormData>(async () => {
    const title = extraTitle.trim();
    if (!title) return "Type the job first.";
    try {
      await mutate("POST", `/api/visits/${date}/extras`, { title }, `Added to ${fmtShort(date)}`, { quiet: true });
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

  function openMeals() {
    goToTab("meals");
    void showWeek(mondayOf(date));
  }

  return (
    <div className="section">
      <Card>
        <div className="visit-head">
          <button
            type="button"
            className="icon-btn"
            aria-label="Previous visit"
            onClick={() => dispatch({ type: "date", date: stepVisit(date, -1) })}
          >
            ‹
          </button>
          <div>
            <h2>{fmtLong(date)}</h2>
            <div className="visit-meta">
              <Pill variant={rel === "Today" || rel === "Next visit" ? "ok" : "default"}>{rel}</Pill>
              <span className="small muted">Meal prep covers {settings[session].covers}</span>
            </div>
          </div>
          <button
            type="button"
            className="icon-btn"
            aria-label="Next visit"
            onClick={() => dispatch({ type: "date", date: stepVisit(date, 1) })}
          >
            ›
          </button>
        </div>
        <div className="row small mt14">
          <span className="mono">
            {doneCount} of {total} done
          </span>
          <span className="spacer" />
          {date !== next ? (
            <button type="button" className="linkish" onClick={() => dispatch({ type: "date", date: next })}>
              Jump to next visit
            </button>
          ) : null}
        </div>
        <div className="progress" aria-hidden="true">
          <i style={{ width: `${percent}%` }} />
        </div>
      </Card>

      {extras.length > 0 || isOwner ? (
        <div className="group">
          <div className="group-h">
            <span className="label">Added for this visit</span>
            <span className="mono small muted">
              {extras.filter(([, x]) => x.done).length}/{extras.length}
            </span>
          </div>
          {extras.map(([id, extra]) => (
            <ExtraRow key={id} date={date} extraId={id} extra={extra} canRemove={isOwner} />
          ))}
          {isOwner ? (
            <>
              <form className="task" action={addExtra} style={{ gridTemplateColumns: "1fr auto" }}>
                <input
                  type="text"
                  id="extraTitle"
                  maxLength={200}
                  value={extraTitle}
                  onChange={(e) => setExtraTitle(e.target.value)}
                  placeholder="Add a one-off job, e.g. Sort the hall closet"
                  aria-label="One-off job for this visit"
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
                showMealsLink={canSeeMeals}
                onOpenMeals={openMeals}
              />
            ))}
          </div>
        ))
      )}

      <PhotosCard key={date} date={date} photos={visit?.photos ?? []} openUploaderOnMount={intent === "photo"} />

      <VisitNote key={date} date={date} note={visit?.note ?? ""} focusOnMount={intent === "note"} isOwner={isOwner} />
    </div>
  );
}
