import { useApp } from "../../state/AppState";
import { AddPersonForm } from "./AddPersonForm";
import { PersonRow } from "./PersonRow";

const LABEL_SUGGESTIONS = ["Housekeeper", "Builder", "Pool service", "Gardener", "Cleaner", "Family"];

export function PeopleCard() {
  const { state } = useApp();
  const users = state.users ?? [];

  return (
    <>
      <div className="group">
        <div className="group-h">
          <span className="label">People</span>
          <span className="mono small muted">{users.length}</span>
        </div>
        {users.map((user) => (
          <PersonRow key={user.id} user={user} />
        ))}
        <AddPersonForm />
        {/* One shared list for the add form and every row's label input. */}
        <datalist id="labelList">
          {LABEL_SUGGESTIONS.map((suggestion) => (
            <option key={suggestion} value={suggestion} />
          ))}
        </datalist>
      </div>
      <p className="small muted">
        Everyone signs in with a code texted to their phone. Owners also have a password as a backup. Staff see Home and
        Visit; turn on Meals for people who cook. Shopping and Setup are owner-only.
      </p>
    </>
  );
}
