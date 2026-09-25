import { useApp } from "../../state/AppState";
import { AccountCard } from "./AccountCard";
import { MealSettingsCard } from "./MealSettingsCard";
import { PeopleCard } from "./PeopleCard";
import { TasksCard } from "./TasksCard";

export function SetupScreen() {
  const { state } = useApp();

  return (
    <div className="section">
      <PeopleCard />
      <TasksCard />
      {/* Not before /api/state has answered: the card would adopt the defaults and save them back. */}
      {state.loadedState ? <MealSettingsCard /> : null}
      <AccountCard />
    </div>
  );
}
