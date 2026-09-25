import { Card } from "../../components/Card";
import { Pill } from "../../components/Pill";
import { addDays, fmt, fmtDay } from "../../lib/dates";
import { useApp } from "../../state/AppState";

export function WeekNav() {
  const { state, showWeek } = useApp();
  const week = state.ui.week;
  const plan = state.plans[week] ?? null;
  const loaded = state.planLoaded[week] === true;

  return (
    <Card className="visit-head">
      <button
        type="button"
        className="icon-btn"
        aria-label="Previous week"
        onClick={() => void showWeek(addDays(week, -7))}
      >
        ‹
      </button>
      <div>
        <div className="label">Week of</div>
        <h2>{fmt(week, { month: "long", day: "numeric" })}</h2>
        <div className="visit-meta">
          <span className="small muted">
            Tue {fmtDay(addDays(week, 1))} and Fri {fmtDay(addDays(week, 4))}
          </span>
          {loaded && !plan ? <Pill>no menu yet</Pill> : null}
        </div>
      </div>
      <button
        type="button"
        className="icon-btn"
        aria-label="Next week"
        onClick={() => void showWeek(addDays(week, 7))}
      >
        ›
      </button>
    </Card>
  );
}
