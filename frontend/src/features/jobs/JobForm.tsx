import { useActionState, useState } from "react";
import type { Job } from "../../api/types";
import { Button } from "../../components/Button";
import { Field } from "../../components/Field";
import { isWebLink } from "../../lib/jobs";
import { useApp } from "../../state/AppState";

/** Owner-only: add a job (no `job`) or edit one. */
export function JobForm({ job, onDone }: { job?: Job; onDone: () => void }) {
  const { mutate } = useApp();
  const prefix = job ? `job${job.id}` : "newJob";
  const [title, setTitle] = useState(job?.title ?? "");
  const [details, setDetails] = useState(job?.details ?? "");
  const [link, setLink] = useState(job?.link ?? "");

  const [error, save, saving] = useActionState<string | null, FormData>(async () => {
    const body = { title: title.trim(), details: details.trim(), link: link.trim() };
    if (!body.title) return "Give the job a title.";
    if (body.link && !isWebLink(body.link)) return "A link has to start with http:// or https://.";
    try {
      // quiet: the failure is shown inline below, so the toast would repeat it.
      if (job) await mutate("PATCH", `/api/jobs/${job.id}`, body, "Job saved", { quiet: true });
      else await mutate("POST", "/api/jobs", body, "Job added", { quiet: true });
      if (!job) {
        setTitle("");
        setDetails("");
        setLink("");
      }
      onDone();
      return null;
    } catch (e) {
      return e instanceof Error ? e.message : "Couldn't save that job.";
    }
  }, null);

  return (
    <form className="job-form" action={save}>
      <Field label="Title" htmlFor={`${prefix}Title`}>
        <input
          id={`${prefix}Title`}
          type="text"
          maxLength={200}
          value={title}
          onChange={(e) => setTitle(e.target.value)}
          placeholder="e.g. Rehang the hall closet door"
          required
        />
      </Field>
      <Field label="Details (optional)" htmlFor={`${prefix}Details`}>
        <textarea
          id={`${prefix}Details`}
          maxLength={4000}
          value={details}
          onChange={(e) => setDetails(e.target.value)}
          placeholder="What needs doing, sizes, materials…"
        />
      </Field>
      <Field label="Link (optional)" htmlFor={`${prefix}Link`} hint="A product page or reference, starting https://">
        <input
          id={`${prefix}Link`}
          type="url"
          inputMode="url"
          maxLength={500}
          value={link}
          onChange={(e) => setLink(e.target.value)}
          placeholder="https://"
        />
      </Field>
      <div className="row">
        <Button type="submit" variant="primary" disabled={saving}>
          {job ? "Save job" : "Add job"}
        </Button>
        <Button variant="ghost" disabled={saving} onClick={onDone}>
          Cancel
        </Button>
      </div>
      {error ? (
        <div className="err" role="alert">
          {error}
        </div>
      ) : null}
    </form>
  );
}
