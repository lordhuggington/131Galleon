import { useActionState, useState } from "react";
import type { User } from "../../api/types";
import { Button } from "../../components/Button";
import { Field } from "../../components/Field";
import { Pill } from "../../components/Pill";
import { prettyPhone } from "../../lib/phone";
import { useApp } from "../../state/AppState";

export function PersonRow({ user }: { user: User }) {
  const { state, dispatch, mutate } = useApp();
  const isSelf = state.me?.id === user.id;
  const [editing, setEditing] = useState(false);
  const [showCode, setShowCode] = useState(false);
  const [displayName, setDisplayName] = useState(user.displayName);
  const [label, setLabel] = useState(user.label);
  const [phone, setPhone] = useState(user.phone ?? "");
  const [doorCode, setDoorCode] = useState(user.doorCode ?? "");
  const [canSeeMeals, setCanSeeMeals] = useState(user.canSeeMeals);
  const [password, setPassword] = useState("");

  const [saveError, saveAction, saving] = useActionState<string | null, FormData>(async () => {
    const name = displayName.trim();
    if (!name) return "A name is required.";
    try {
      // An empty string clears the stored phone or door code (spec §9).
      // quiet: the failure is shown inline below, so the toast would repeat it.
      await mutate(
        "PATCH",
        `/api/users/${user.id}`,
        {
          displayName: name,
          label: label.trim(),
          phone: phone.trim(),
          doorCode: doorCode.trim(),
          canSeeMeals,
        },
        "Saved",
        { quiet: true },
      );
      setEditing(false);
      return null;
    } catch (e) {
      return e instanceof Error ? e.message : "Couldn't save that.";
    }
  }, null);

  const [pwError, pwAction, settingPassword] = useActionState<string | null, FormData>(async () => {
    try {
      // quiet: the failure is shown inline beside the field.
      await mutate(
        "PATCH",
        `/api/users/${user.id}`,
        { password },
        "Password reset. They'll need to sign in again.",
        { quiet: true },
      );
      setPassword("");
      dispatch({ type: "confirm", key: null });
      return null;
    } catch (e) {
      return e instanceof Error ? e.message : "Couldn't reset that password.";
    }
  }, null);

  function setActive(active: boolean) {
    dispatch({ type: "user-active", userId: user.id, active });
    void mutate("PATCH", `/api/users/${user.id}`, { active }, active ? "Access restored" : "Access removed").catch(
      () => {},
    );
  }

  if (editing) {
    return (
      <form className="uedit" action={saveAction}>
        <Field label="Name" htmlFor={`pn-${user.id}`}>
          <input
            id={`pn-${user.id}`}
            type="text"
            maxLength={80}
            value={displayName}
            onChange={(e) => setDisplayName(e.target.value)}
          />
        </Field>
        <Field label="Label" htmlFor={`pl-${user.id}`}>
          <input
            id={`pl-${user.id}`}
            type="text"
            maxLength={40}
            list="labelList"
            value={label}
            onChange={(e) => setLabel(e.target.value)}
          />
        </Field>
        <Field label="Phone" htmlFor={`pp-${user.id}`} hint="for text-message sign-in">
          <input
            id={`pp-${user.id}`}
            type="tel"
            inputMode="tel"
            maxLength={20}
            value={phone}
            onChange={(e) => setPhone(e.target.value)}
          />
        </Field>
        <Field label="Door code" htmlFor={`pc-${user.id}`} hint="4–8 digits, or blank to clear">
          <input
            id={`pc-${user.id}`}
            type="text"
            inputMode="numeric"
            maxLength={8}
            value={doorCode}
            onChange={(e) => setDoorCode(e.target.value)}
          />
        </Field>
        {/* Owners always see Meals, so the toggle is only meaningful on staff rows. */}
        {user.role === "staff" ? (
          <label className="field" htmlFor={`pm-${user.id}`}>
            <span>Can see Meals</span>
            <input
              id={`pm-${user.id}`}
              type="checkbox"
              checked={canSeeMeals}
              onChange={(e) => setCanSeeMeals(e.target.checked)}
            />
          </label>
        ) : null}
        <div className="row">
          <Button type="submit" variant="primary" disabled={saving}>
            Save
          </Button>
          <Button
            variant="ghost"
            onClick={() => {
              setDisplayName(user.displayName);
              setLabel(user.label);
              setPhone(user.phone ?? "");
              setDoorCode(user.doorCode ?? "");
              setCanSeeMeals(user.canSeeMeals);
              setEditing(false);
            }}
          >
            Cancel
          </Button>
        </div>
        {saveError ? (
          <div className="err" role="alert">
            {saveError}
          </div>
        ) : null}
      </form>
    );
  }

  return (
    <div className="urow">
      <div>
        <div className="t">
          {user.displayName} {user.active ? null : <Pill variant="warn">signed out · no access</Pill>}
        </div>
        <div className="small muted mono">{user.username}</div>
        <div className="small muted">
          {user.phone ? (
            <span className="mono">{prettyPhone(user.phone)}</span>
          ) : (
            "no phone yet — can't sign in by text"
          )}
        </div>
        <div className="row small mt6">
          <span>
            Door code:{" "}
            {user.doorCode ? (
              <button type="button" className="linkish mono" onClick={() => setShowCode((v) => !v)}>
                {showCode ? user.doorCode : "••••"}
              </button>
            ) : (
              <span className="muted">none</span>
            )}
          </span>
          <span className="muted">Meals {user.role === "owner" || user.canSeeMeals ? "✓" : "✗"}</span>
        </div>
      </div>
      <div className="row">
        <Pill>{user.label || (user.role === "owner" ? "Owner" : "Staff")}</Pill>
        <Pill variant={user.role === "owner" ? "ok" : "oat"}>{user.role === "owner" ? "Owner" : "Staff"}</Pill>
      </div>
      {isSelf ? (
        <span className="small muted">You</span>
      ) : state.ui.confirm === `pw:${user.id}` ? (
        <form className="row" action={pwAction}>
          <input
            type="password"
            aria-label={`New password for ${user.displayName}`}
            placeholder="New password (10+)"
            autoComplete="new-password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
          />
          <Button type="submit" disabled={settingPassword}>
            Set
          </Button>
          <Button variant="ghost" onClick={() => dispatch({ type: "confirm", key: null })}>
            Cancel
          </Button>
          {pwError ? (
            <div className="err" role="alert">
              {pwError}
            </div>
          ) : null}
        </form>
      ) : (
        <span className="row">
          <Button variant="ghost" onClick={() => setEditing(true)}>
            Edit
          </Button>
          {user.role === "owner" ? (
            <Button variant="ghost" onClick={() => dispatch({ type: "confirm", key: `pw:${user.id}` })}>
              Reset password
            </Button>
          ) : null}
          <Button variant="ghost" onClick={() => setActive(!user.active)}>
            {user.active ? "Remove access" : "Restore access"}
          </Button>
        </span>
      )}
    </div>
  );
}
