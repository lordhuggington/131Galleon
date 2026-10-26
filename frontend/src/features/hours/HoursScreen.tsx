import { useMemo } from "react";
import { Button } from "../../components/Button";
import { Card } from "../../components/Card";
import { fmt, mondayOf, today } from "../../lib/dates";
import { groupByWeek, hoursLabel, minutesInWeek, minutesWith, summarise, type WeekGroup } from "../../lib/hours";
import { useApp, useIsOwner } from "../../state/AppState";
import { ClaimForm } from "./ClaimForm";
import { ClaimRow } from "./ClaimRow";

const weekTitle = (week: string): string => `Week of ${fmt(week, { month: "short", day: "numeric" })}`;

function WeekCard({ group, isOwner }: { group: WeekGroup; isOwner: boolean }) {
  const { dispatch, mutateAll } = useApp();
  const waiting = group.submittedIds.length;

  function approveAll() {
    const ids = group.submittedIds;
    dispatch({ type: "claim-status", claimIds: ids, status: "approved" });
    void mutateAll(
      ids.map((id) => ({ method: "PATCH" as const, path: `/api/hours/${id}`, body: { status: "approved" } })),
      ids.length === 1 ? "Approved" : `${ids.length} claims approved`,
    ).catch(() => {});
  }

  return (
    <div className="group">
      <div className="group-h">
        <span className="label">{weekTitle(group.week)}</span>
        <span className="mono small">{hoursLabel(group.minutes)}</span>
      </div>
      {isOwner ? (
        <div className="week-people">
          {group.people.map(({ person, minutes }) => (
            <span key={person.id} className="small">
              {person.displayName} <span className="mono">{hoursLabel(minutes)}</span>
            </span>
          ))}
          <span className="spacer" />
          {waiting > 0 ? (
            <Button variant="primary" onClick={approveAll}>
              Approve all ({waiting})
            </Button>
          ) : null}
        </div>
      ) : null}
      {group.claims.map((claim) => (
        <ClaimRow key={claim.id} claim={claim} />
      ))}
    </div>
  );
}

export function HoursScreen() {
  const { state } = useApp();
  const isOwner = useIsOwner();
  const claims = state.hours;
  const groups = useMemo(() => groupByWeek(claims ?? []), [claims]);
  const summary = useMemo(() => summarise(claims ?? []), [claims]);
  const thisWeek = minutesInWeek(claims ?? [], mondayOf(today()));

  return (
    <div className="section">
      <Card>
        <h2 className="screen-h">Hours</h2>
        {isOwner ? (
          <div className="stats mt10">
            <div>
              <div className="label">To approve</div>
              <div className="stat mono">{hoursLabel(summary.toApprove)}</div>
              <div className="small muted">
                {summary.toApproveCount} {summary.toApproveCount === 1 ? "claim" : "claims"}
              </div>
            </div>
            <div>
              <div className="label">Approved, not paid</div>
              <div className="stat mono">{hoursLabel(summary.approvedUnpaid)}</div>
            </div>
          </div>
        ) : (
          <>
            <div className="stats mt10">
              <div>
                <div className="label">This week</div>
                <div className="stat mono">{hoursLabel(thisWeek)}</div>
              </div>
              <div>
                <div className="label">Waiting for approval</div>
                <div className="stat mono">{hoursLabel(minutesWith(claims ?? [], "submitted"))}</div>
              </div>
            </div>
            <ClaimForm />
          </>
        )}
      </Card>

      {claims === null ? (
        <div className="empty">Loading hours…</div>
      ) : groups.length === 0 ? (
        <Card className="empty">
          {isOwner ? "No hours claimed yet." : "No hours claimed yet. Add the first day above."}
        </Card>
      ) : (
        groups.map((group) => <WeekCard key={group.week} group={group} isOwner={isOwner} />)
      )}
    </div>
  );
}
