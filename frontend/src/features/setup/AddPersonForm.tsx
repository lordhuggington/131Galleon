import { useActionState, useState } from "react";
import type { Role } from "../../api/types";
import { Button } from "../../components/Button";
import { Field } from "../../components/Field";
import { useApp } from "../../state/AppState";

export function AddPersonForm() {
  const { mutate } = useApp();
  const [displayName, setDisplayName] = useState("");
  const [label, setLabel] = useState("Housekeeper");
  const [role, setRole] = useState<Role>("staff");
  const [phone, setPhone] = useState("");
  const [doorCode, setDoorCode] = useState("");
  const [canSeeMeals, setCanSeeMeals] = useState(true);
  const [password, setPassword] = useState("");

  const [error, addPerson, adding] = useActionState<string | null, FormData>(async () => {
    const name = displayName.trim();
    if (!name) return "Type their name first.";
    const body: Record<string, unknown> = {
      displayName: name,
      label: label.trim(),
      role,
      canSeeMeals: role === "owner" ? true : canSeeMeals,
    };
    if (phone.trim()) body.phone = phone.trim();
    if (doorCode.trim()) body.doorCode = doorCode.trim();
    if (role === "owner") body.password = password;
    try {
      // quiet: this form shows the failure inline, so the toast would repeat it.
      await mutate("POST", "/api/users", body, "Person added", { quiet: true });
      setDisplayName("");
      setLabel("Housekeeper");
      setRole("staff");
      setPhone("");
      setDoorCode("");
      setCanSeeMeals(true);
      setPassword("");
      return null;
    } catch (e) {
      return e instanceof Error ? e.message : "Couldn't add that person.";
    }
  }, null);

  return (
    <form className="uedit" action={addPerson}>
      <Field label="Name" htmlFor="npName">
        <input
          id="npName"
          type="text"
          maxLength={80}
          value={displayName}
          onChange={(e) => setDisplayName(e.target.value)}
          placeholder="e.g. Maria"
          required
        />
      </Field>
      <Field label="Label" htmlFor="npLabel">
        <input
          id="npLabel"
          type="text"
          maxLength={40}
          list="labelList"
          value={label}
          onChange={(e) => setLabel(e.target.value)}
        />
      </Field>
      <Field label="Type" htmlFor="npRole">
        <select id="npRole" value={role} onChange={(e) => setRole(e.target.value as Role)}>
          <option value="staff">Staff</option>
          <option value="owner">Owner</option>
        </select>
      </Field>
      <Field label="Phone" htmlFor="npPhone" hint="for text-message sign-in">
        <input
          id="npPhone"
          type="tel"
          inputMode="tel"
          maxLength={20}
          value={phone}
          onChange={(e) => setPhone(e.target.value)}
          placeholder="(310) 555-1234"
        />
      </Field>
      <Field label="Door code" htmlFor="npCode" hint="4–8 digits">
        <input
          id="npCode"
          type="text"
          inputMode="numeric"
          maxLength={8}
          value={doorCode}
          onChange={(e) => setDoorCode(e.target.value)}
        />
      </Field>
      {role === "staff" ? (
        <label className="field" htmlFor="npMeals">
          <span>Can see Meals</span>
          <input
            id="npMeals"
            type="checkbox"
            checked={canSeeMeals}
            onChange={(e) => setCanSeeMeals(e.target.checked)}
          />
        </label>
      ) : (
        <Field label="Password" htmlFor="npPass" hint="10+ characters; owners keep a password as a backup">
          <input
            id="npPass"
            type="password"
            autoComplete="new-password"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            required
          />
        </Field>
      )}
      <Button type="submit" variant="primary" disabled={adding}>
        Add person
      </Button>
      {error ? (
        <div className="err" role="alert">
          {error}
        </div>
      ) : null}
    </form>
  );
}
