import { useActionState, useMemo, useState } from "react";
import type { Job, JobComment, Role } from "../../api/types";
import { Button } from "../../components/Button";
import { InlineConfirm } from "../../components/InlineConfirm";
import { Pill } from "../../components/Pill";
import { fmtStamp } from "../../lib/dates";
import { fmtAgo, isOwnerComment } from "../../lib/jobs";
import { useApp } from "../../state/AppState";

export function JobComments({ job }: { job: Job }) {
  const { state, dispatch, mutate } = useApp();
  const me = state.me;
  const [body, setBody] = useState("");
  // Owners get the people list on this tab, which says who among the other commenters is an owner.
  const roles = useMemo(
    () => (state.users ? new Map<number, Role>(state.users.map((u) => [u.id, u.role])) : null),
    [state.users],
  );

  const [error, post, posting] = useActionState<string | null, FormData>(async () => {
    const text = body.trim();
    if (!text) return "Type a comment first.";
    try {
      // quiet: the failure is shown inline under the box.
      await mutate("POST", `/api/jobs/${job.id}/comments`, { body: text }, "Comment posted", { quiet: true });
      setBody("");
      return null;
    } catch (e) {
      return e instanceof Error ? e.message : "Couldn't post that comment.";
    }
  }, null);

  if (!me) return null;

  function canDelete(comment: JobComment): boolean {
    return me !== null && (me.role === "owner" || comment.by.id === me.id);
  }

  function remove(comment: JobComment) {
    dispatch({ type: "remove-job-comment", jobId: job.id, commentId: comment.id });
    void mutate("DELETE", `/api/jobs/${job.id}/comments/${comment.id}`, undefined, "Comment deleted").catch(() => {});
  }

  const now = new Date();

  return (
    <div className="comments">
      <div className="row">
        <span className="label">Comments</span>
        <span className="mono small muted">{job.comments.length}</span>
      </div>

      {job.comments.map((comment) => {
        const fromOwner = isOwnerComment(comment, me, roles);
        return (
          <div key={comment.id} className={fromOwner ? "comment from-owner" : "comment"}>
            <div className="row small">
              <span className="t">{comment.by.displayName}</span>
              {fromOwner ? <Pill variant="oat">Homeowner</Pill> : <Pill variant="ok">Builder</Pill>}
              <span className="spacer" />
              <time className="muted" dateTime={comment.createdAt} title={fmtStamp(comment.createdAt)}>
                {fmtAgo(comment.createdAt, now)}
              </time>
            </div>
            <div className="comment-body">{comment.body}</div>
            {canDelete(comment) ? (
              <div className="row">
                {state.ui.confirm === `jc:${comment.id}` ? (
                  <InlineConfirm
                    confirmLabel="Delete"
                    onConfirm={() => remove(comment)}
                    onCancel={() => dispatch({ type: "confirm", key: null })}
                  />
                ) : (
                  <button
                    type="button"
                    className="linkish"
                    onClick={() => dispatch({ type: "confirm", key: `jc:${comment.id}` })}
                  >
                    Delete
                  </button>
                )}
              </div>
            ) : null}
          </div>
        );
      })}

      <form className="comment-form" action={post}>
        <label className="sr-only" htmlFor={`jobComment${job.id}`}>
          Comment on {job.title}
        </label>
        <textarea
          id={`jobComment${job.id}`}
          maxLength={2000}
          value={body}
          onChange={(e) => setBody(e.target.value)}
          placeholder={me.role === "owner" ? "Add a note for the builder…" : "Ask a question or post an update…"}
        />
        <div className="row">
          <Button type="submit" variant="primary" disabled={posting || !body.trim()}>
            Post comment
          </Button>
        </div>
        {error ? (
          <div className="err" role="alert">
            {error}
          </div>
        ) : null}
      </form>
    </div>
  );
}
