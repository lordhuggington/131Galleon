import { useMemo, useState } from "react";
import { Button } from "../../components/Button";
import { Card } from "../../components/Card";
import { Chips } from "../../components/Chips";
import { countJobs, numberJobs, type JobFilter } from "../../lib/jobs";
import { useApp, useIsOwner } from "../../state/AppState";
import { JobCard } from "./JobCard";
import { JobForm } from "./JobForm";

export function JobsScreen() {
  const { state } = useApp();
  const isOwner = useIsOwner();
  const [filter, setFilter] = useState<JobFilter>("all");
  const [openId, setOpenId] = useState<number | null>(null);
  const [adding, setAdding] = useState(false);
  const jobs = state.jobs;

  const counts = useMemo(() => countJobs(jobs ?? []), [jobs]);
  const shown = useMemo(() => numberJobs(jobs ?? [], filter), [jobs, filter]);

  return (
    <div className="section">
      <Card>
        <div className="row">
          <h2 className="screen-h">Jobs</h2>
          <span className="spacer" />
          {isOwner && !adding ? (
            <Button variant="primary" onClick={() => setAdding(true)}>
              Add a job
            </Button>
          ) : null}
        </div>
        <div className="row small mt6">
          <span className="mono">
            {counts.todo} to do · {counts.doing} in progress · {counts.done} done
          </span>
        </div>
        {isOwner && adding ? <JobForm onDone={() => setAdding(false)} /> : null}
      </Card>

      <Chips<JobFilter>
        label="Show jobs"
        value={filter}
        onChange={setFilter}
        options={[
          { value: "all", label: `All · ${counts.all}` },
          { value: "todo", label: `To do · ${counts.todo}` },
          { value: "doing", label: `In progress · ${counts.doing}` },
          { value: "done", label: `Done · ${counts.done}` },
        ]}
      />

      {jobs === null ? (
        <div className="empty">Loading jobs…</div>
      ) : jobs.length === 0 ? (
        <Card className="empty">{isOwner ? "No jobs yet. Add the first one above." : "No jobs yet."}</Card>
      ) : shown.length === 0 ? (
        <Card className="empty">Nothing here.</Card>
      ) : (
        <div className="jobs">
          {shown.map(({ number, job }) => (
            <JobCard
              key={job.id}
              number={number}
              job={job}
              open={openId === job.id}
              onToggle={() => setOpenId((current) => (current === job.id ? null : job.id))}
            />
          ))}
        </div>
      )}
    </div>
  );
}
