// Response shapes the app consumes. Field names must match app/api.py and app/store.py exactly.

export type Role = "owner" | "staff";

export interface Me {
  id: number;
  username: string;
  displayName: string;
  role: Role;
  label: string;
  phone: string | null;
  doorCode: string | null;
  canSeeMeals: boolean;
}

/** Owner-only /api/users rows: everything in Me plus admin flags. */
export interface User extends Me {
  active: boolean;
  hasPassword: boolean;
}

export type Freq = "visit" | "weekly" | "fortnightly" | "monthly";
export type Day = "any" | "tue" | "fri";

export interface Task {
  id: string;
  title: string;
  area: string;
  freq: Freq;
  day: Day;
  notes: string;
  order: number;
  active: boolean;
  /** Only present on the seeded meal-prep task; "meals" links the row to the Meals tab. */
  link?: string;
}

export interface Extra {
  title: string;
  notes: string;
  done: boolean;
  createdAt: string;
}

export type PhotoKind = "done" | "fix";

export interface PhotoAuthor {
  id: number;
  displayName: string;
}

export interface Photo {
  id: number;
  kind: PhotoKind;
  caption: string;
  /** Server-built path, e.g. "/photos/ab12….jpg". */
  url: string;
  createdAt: string;
  by: PhotoAuthor;
}

export interface Visit {
  date: string;
  note: string;
  /** taskId -> ISO timestamp it was ticked. */
  done: Record<string, string>;
  /** extraId (stringified row id) -> extra. */
  extras: Record<string, Extra>;
  photos: Photo[];
}

export interface SessionPortions {
  breakfast: number;
  main: number;
  dessert: number;
  covers: string;
}

export interface Settings {
  kcal: number;
  protein: number;
  tue: SessionPortions;
  fri: SessionPortions;
  store: string;
  likes: string;
  dislikes: string;
  pantry: string;
}

export interface Ingredient {
  item: string;
  amount: string;
  kcal: number;
  protein: number;
}

export interface Recipe {
  title: string;
  blurb: string;
  portions: number;
  portionNote: string;
  ingredients: Ingredient[];
  steps: string[];
  storage: string;
  fav: boolean;
}

export type Slot = "breakfast" | "main" | "dessert";
export type SessionKey = "tue" | "fri";

export interface PlanSession {
  date: string;
  covers: string;
  timeline: string[];
  recipes: Partial<Record<Slot, Recipe>>;
  /** What this session's cook is expected to leave behind, for the next session to use up. */
  leftovers: string[];
}

export interface ShoppingItem {
  id: string;
  item: string;
  buy: string;
  aisle: string;
  /** Which prep day needs it. */
  for: "both" | "tue" | "fri";
  stock: boolean;
  /** Amazon Fresh search phrase; "" on plans written before the menu asked for one. */
  search: string;
}

export interface Plan {
  week: string;
  source: string;
  note: string;
  createdAt: string;
  sessions: Partial<Record<SessionKey, PlanSession>>;
  /** Owner-only. */
  shopping?: ShoppingItem[];
  /** Owner-only: itemId -> true. */
  got?: Record<string, boolean>;
}

export interface MeResponse { me: Me }
export interface StateResponse {
  me: Me;
  tasks: Task[];
  visits: Record<string, Visit>;
  settings: Settings;
}
export interface PlanResponse { plan: Plan | null }
export interface UsersResponse { users: User[] }
export interface UserResponse { user: User }
export interface TaskResponse { task: Task }
export interface SettingsResponse { settings: Settings }
export interface LoginOptions { sms: boolean }
export interface PhotoResponse { photo: Photo }
export interface OkResponse { ok: true }
/** POST /api/visits/{date}/extras returns the new row id as a string. */
export interface ExtraCreated { id: string }
