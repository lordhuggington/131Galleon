import { AccountCard } from "./AccountCard";
import { PeopleCard } from "./PeopleCard";
import { TasksCard } from "./TasksCard";

export function SetupScreen() {
  return (
    <div className="section">
      <PeopleCard />
      <TasksCard />
      <AccountCard />
    </div>
  );
}
