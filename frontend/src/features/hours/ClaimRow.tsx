import { useState } from "react";
import type { ClaimStatus, HoursClaim } from "../../api/types";
import { Button } from "../../components/Button";
import { InlineConfirm } from "../../components/InlineConfirm";
import { Pill } from "../../components/Pill";
import { fmtShort } from "../../lib/dates";
import { STATUS_LABEL, hoursLabel, ownerMoves } from "../../lib/hours";
import { useApp, useIsOwner } from "../../state/AppState";
import { ClaimForm } from "./ClaimForm";

const STATUS_PILL: Record<ClaimStatus, "default" | "ok" | "oat"> = {
  submitted: "default",
  approved: "ok",
  paid: "oat",
};

export function ClaimStatusPill({ status }: { status: ClaimStatus }) {
  return <Pill variant={STATUS_PILL[status]}>{STATUS_LABEL[status]}</Pill>;
}

export function ClaimRow({ claim }: { claim: HoursClaim }) {
  const { state, dispatch, mutate } = useApp();
  const isOwner = useIsOwner();
  const [editing, setEditing] = useState(false);
  const confirming = state.ui.confirm === `claim:${claim.id}`;
  // The claimant may change or withdraw a claim until an owner has approved it; owners may always delete.
  const mine = state.me?.id === claim.by.id;
  const canEdit = !isOwner && mine && claim.status === "submitted";
  const canDelete = isOwner || canEdit;

  function move(status: ClaimStatus) {
    dispatch({ type: "claim-status", claimIds: [claim.id], status });
    const verb = status === "approved" ? "Approved" : status === "paid" ? "Marked paid" : "Moved back";
    void mutate("PATCH", `/api/hours/${claim.id}`, { status }, verb).catch(() => {});
  }

  function remove() {
    dispatch({ type: "remove-claim", claimId: claim.id });
    void mutate("DELETE", `/api/hours/${claim.id}`, undefined, "Claim deleted").catch(() => {});
  }

  if (editing) {
    return (
      <div className="claim">
        <ClaimForm claim={claim} onDone={() => setEditing(false)} />
      </div>
    );
  }

  const stamps: string[] = [];
  if (claim.approvedBy) stamps.push(`approved by ${claim.approvedBy.displayName}`);
  if (claim.paidBy) stamps.push(`paid by ${claim.paidBy.displayName}`);

  return (
    <div className="claim">
      <div className="claim-main">
        <div className="row">
          <span className="t">{fmtShort(claim.date)}</span>
          {isOwner ? <span className="small muted">{claim.by.displayName}</span> : null}
        </div>
        {claim.note ? <div className="n">{claim.note}</div> : null}
        {isOwner && stamps.length ? <div className="small muted">{stamps.join(" · ")}</div> : null}
      </div>
      <div className="claim-side">
        <span className="mono claim-hours">{hoursLabel(claim.minutes)}</span>
        <ClaimStatusPill status={claim.status} />
      </div>
      {isOwner || canDelete ? (
        <div className="row claim-actions">
          {isOwner
            ? ownerMoves(claim.status).map((m, index) => (
                <Button key={m.to} variant={index === 0 ? "secondary" : "ghost"} onClick={() => move(m.to)}>
                  {m.label}
                </Button>
              ))
            : null}
          {canEdit ? (
            <Button variant="ghost" onClick={() => setEditing(true)}>
              Edit
            </Button>
          ) : null}
          {canDelete ? (
            confirming ? (
              <InlineConfirm
                confirmLabel="Delete"
                onConfirm={remove}
                onCancel={() => dispatch({ type: "confirm", key: null })}
              />
            ) : (
              <Button variant="ghost" onClick={() => dispatch({ type: "confirm", key: `claim:${claim.id}` })}>
                Delete
              </Button>
            )
          ) : null}
        </div>
      ) : null}
    </div>
  );
}
