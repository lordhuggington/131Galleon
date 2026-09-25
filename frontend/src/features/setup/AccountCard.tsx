import { useActionState, useState } from "react";
import { api } from "../../api/client";
import type { OkResponse } from "../../api/types";
import { Button } from "../../components/Button";
import { Card } from "../../components/Card";
import { Field } from "../../components/Field";
import { prettyPhone } from "../../lib/phone";
import { useApp } from "../../state/AppState";
import { useToast } from "../../state/useToast";

export function AccountCard() {
  const { state, signOut } = useApp();
  const { toast } = useToast();
  const me = state.me;
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [open, setOpen] = useState(false);

  const [error, changePassword, changing] = useActionState<string | null, FormData>(async () => {
    try {
      await api<OkResponse>("PUT", "/api/me/password", { current, new: next });
      setCurrent("");
      setNext("");
      setOpen(false);
      toast("Password changed.");
      return null;
    } catch (e) {
      return e instanceof Error ? e.message : "Couldn't change that password.";
    }
  }, null);

  if (!me) return null;

  return (
    <Card>
      <h3 className="h19">Account</h3>
      <p className="small muted mt6">
        {me.displayName} · {me.label || "Owner"}
      </p>
      <p className="small">
        <span className="label">Phone</span>{" "}
        <span className="mono">{me.phone ? prettyPhone(me.phone) : "No phone on file yet"}</span>
      </p>
      {open ? (
        <form className="section" action={changePassword}>
          <Field label="Current password" htmlFor="pwCur">
            <input
              id="pwCur"
              type="password"
              autoComplete="current-password"
              value={current}
              onChange={(e) => setCurrent(e.target.value)}
            />
          </Field>
          <Field label="New password (10+ characters)" htmlFor="pwNew">
            <input
              id="pwNew"
              type="password"
              autoComplete="new-password"
              value={next}
              onChange={(e) => setNext(e.target.value)}
            />
          </Field>
          {error ? (
            <div className="err" role="alert">
              {error}
            </div>
          ) : null}
          <div className="row">
            <Button type="submit" variant="primary" disabled={changing}>
              Change password
            </Button>
            <Button variant="ghost" onClick={() => setOpen(false)}>
              Close
            </Button>
          </div>
        </form>
      ) : (
        <div className="row mt12">
          <Button onClick={() => setOpen(true)}>Change password</Button>
          <Button variant="ghost" onClick={() => void signOut()}>
            Sign out
          </Button>
        </div>
      )}
    </Card>
  );
}
