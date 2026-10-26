// Which screens a person gets. The server enforces the same split (spec §1); this only decides
// what to draw. Owners see everything; staff see the run sheet that matches their kind.
import type { Kind, Role } from "../api/types";

export type Tab = "home" | "visit" | "jobs" | "hours" | "setup";
export const TABS: Tab[] = ["home", "visit", "jobs", "hours", "setup"];

export interface Who {
  role: Role;
  kind: Kind;
}

export const isOwner = (who: Who): boolean => who.role === "owner";
// Staff default to housekeeper (the column's default), so only an explicit builder loses the visit.
const isBuilder = (who: Who): boolean => who.kind === "builder";
export const seesVisit = (who: Who): boolean => isOwner(who) || !isBuilder(who);
export const seesJobs = (who: Who): boolean => isOwner(who) || isBuilder(who);
export const seesHours = (who: Who): boolean => isOwner(who) || !isBuilder(who);

/** The tabs this person may open, in bottom-nav order. */
export function tabsFor(who: Who): Tab[] {
  return TABS.filter((tab) => {
    switch (tab) {
      case "home":
        return true;
      case "visit":
        return seesVisit(who);
      case "jobs":
        return seesJobs(who);
      case "hours":
        return seesHours(who);
      case "setup":
        return isOwner(who);
    }
  });
}
