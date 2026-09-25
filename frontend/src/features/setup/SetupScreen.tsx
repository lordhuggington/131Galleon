import { AccountCard } from "./AccountCard";
import { MealSettingsCard } from "./MealSettingsCard";
import { PeopleCard } from "./PeopleCard";
import { TasksCard } from "./TasksCard";

export function SetupScreen() {
  return (
    <div className="section">
      <PeopleCard />
      <TasksCard />
      <MealSettingsCard />
      <AccountCard />
    </div>
  );
}
