import { useState } from "react";
import type { Job, JobStatus } from "../../api/types";
import { Button } from "../../components/Button";
import { Chips } from "../../components/Chips";
import { InlineConfirm } from "../../components/InlineConfirm";
import { Pill } from "../../components/Pill";
import { fmtStamp } from "../../lib/dates";
import { JOB_STATUSES, STATUS_LABEL, isWebLink } from "../../lib/jobs";
import { useApp, useIsOwner } from "../../state/AppState";
import { JobComments } from "./JobComments";
import { JobForm } from "./JobForm";
import { JobPhotos } from "./JobPhotos";

const STATUS_PILL: Record<JobStatus, "default" | "ok" | "oat"> = { todo: "default", doing: "oat", done: "ok" };

export function StatusPill({ status }: { status: JobStatus }) {
  return <Pill variant={STATUS_PILL[status]}>{STATUS_LABEL[status]}</Pill>;
}

export function JobCard({
  number,
  job,
  open,
  onToggle,
}: {
  number: number;
  job: Job;
  open: boolean;
  onToggle: () => void;
}) {
  const { state, dispatch, mutate } = useApp();
  const isOwner = useIsOwner();
  const [editing, setEditing] = useState(false);
  const bodyId = `job-body-${job.id}`;

  function setStatus(status: JobStatus) {
    if (status === job.status) return;
    dispatch({ type: "job-status", jobId: job.id, status });
    void mutate("PATCH", `/api/jobs/${job.id}`, { status }, `Marked ${STATUS_LABEL[status].toLowerCase()}`).catch(
      () => {},
    );
  }

  function remove() {
    dispatch({ type: "remove-job", jobId: job.id });
    void mutate("DELETE", `/api/jobs/${job.id}`, undefined, "Job deleted").catch(() => {});
  }

  return (
    <div className={job.status === "done" ? "job done" : "job"}>
      <button type="button" className="job-head" aria-expanded={open} aria-controls={bodyId} onClick={onToggle}>
        <span className="job-num mono" aria-hidden="true">
          {number}
        </span>
        <span className="job-title">
          <span className="sr-only">Job {number}: </span>
          {job.title}
        </span>
        <span className="job-side">
          <StatusPill status={job.status} />
          {job.comments.length || job.photos.length ? (
            <span className="small muted mono">
              {job.comments.length ? `💬 ${job.comments.length}` : ""}
              {job.comments.length && job.photos.length ? " · " : ""}
              {job.photos.length ? `📷 ${job.photos.length}` : ""}
            </span>
          ) : null}
        </span>
      </button>

      {open ? (
        <div className="job-body" id={bodyId}>
          {editing ? (
            <JobForm job={job} onDone={() => setEditing(false)} />
          ) : (
            <>
              {job.details ? <div className="job-details">{job.details}</div> : null}
              {isWebLink(job.link) ? (
                <a className="linkish" href={job.link} target="_blank" rel="noopener noreferrer">
                  Open link ↗
                </a>
              ) : null}
            </>
          )}

          <div className="field">
            <span>Status</span>
            <Chips<JobStatus>
              label={`Status of ${job.title}`}
              value={job.status}
              onChange={setStatus}
              options={JOB_STATUSES.map((s) => ({ value: s, label: STATUS_LABEL[s] }))}
            />
            {job.status === "done" && job.doneAt ? (
              <small>
                Done {job.doneBy ? `by ${job.doneBy.displayName} ` : ""}· {fmtStamp(job.doneAt)}
              </small>
            ) : null}
          </div>

          <JobPhotos job={job} />
          <JobComments job={job} />

          {isOwner && !editing ? (
            <div className="row">
              <Button variant="ghost" onClick={() => setEditing(true)}>
                Edit job
              </Button>
              {state.ui.confirm === `job:${job.id}` ? (
                <InlineConfirm
                  question="Delete this job?"
                  confirmLabel="Delete"
                  onConfirm={remove}
                  onCancel={() => dispatch({ type: "confirm", key: null })}
                />
              ) : (
                <Button variant="ghost" onClick={() => dispatch({ type: "confirm", key: `job:${job.id}` })}>
                  Delete job
                </Button>
              )}
            </div>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}
