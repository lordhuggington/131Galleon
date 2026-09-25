import { useMemo, useState } from "react";
import { fmtShort, fmtWeekday, mondayOf, nextVisit, today } from "../../lib/dates";
import { doneDatesByTask, isDue, sortTasks } from "../../lib/schedule";
import { SunsetBand } from "../../components/illustrations/SunsetBand";
import { useApp, useCanSeeMeals, useIsOwner } from "../../state/AppState";
import { AccountPanel } from "../auth/AccountPanel";
import { DoorCodeBanner } from "./DoorCodeBanner";
import { Tile } from "./Tile";

interface TileSpec {
  key: string;
  icon: string;
  title: string;
  subtitle: string;
  onClick: () => void;
}

export function HomeScreen() {
  const { state, goToTab, dispatch } = useApp();
  const isOwner = useIsOwner();
  const canSeeMeals = useCanSeeMeals();
  const [accountOpen, setAccountOpen] = useState(false);
  const me = state.me;

  const t0 = today();
  const visitDate = nextVisit(t0);
  // The next visit's week — the same week Meals opens on and refresh() loads for Home.
  const week = mondayOf(visitDate);
  const plan = state.plans[week] ?? null;
  const planLoaded = state.planLoaded[week] === true;
  const visit = state.visits[visitDate];

  const readyCount = useMemo(() => {
    const doneMap = doneDatesByTask(state.visits);
    const due = sortTasks(Object.values(state.tasks)).filter((t) => isDue(t, visitDate, doneMap, state.visits));
    const undoneTasks = due.filter((t) => !visit?.done[t.id]).length;
    const undoneExtras = Object.values(visit?.extras ?? {}).filter((x) => !x.done).length;
    return undoneTasks + undoneExtras;
  }, [state.tasks, state.visits, visit, visitDate]);

  if (!me) return null;

  const firstName = me.displayName.split(" ")[0] ?? me.displayName;
  const photoCount = visit?.photos.length ?? 0;
  const visitTitle = visitDate === t0 ? "Today's visit" : `${fmtWeekday(visitDate)}'s visit`;
  const visitSubtitle = `${readyCount} tasks ready${photoCount ? ` · ${photoCount} photos` : ""}`;
  const mealsSubtitle = planLoaded && !plan ? "No menu yet" : "Menu & prep order";
  const shoppingLeft = (plan?.shopping ?? []).filter((i) => !plan?.got?.[i.id]).length;
  const shoppingSubtitle = planLoaded && !plan ? "No menu yet" : `${shoppingLeft} items left to get`;

  function openVisit(intent: "photo" | "note" | null) {
    dispatch({ type: "date", date: visitDate });
    goToTab("visit", intent);
  }

  const tiles: TileSpec[] = [
    { key: "visit", icon: "✅", title: visitTitle, subtitle: visitSubtitle, onClick: () => openVisit(null) },
  ];
  if (canSeeMeals) {
    tiles.push({
      key: "meals",
      icon: "🍳",
      title: "This week's meals",
      subtitle: mealsSubtitle,
      onClick: () => goToTab("meals"),
    });
  }
  if (isOwner) {
    tiles.push({
      key: "shopping",
      icon: "🛒",
      title: "Shopping list",
      subtitle: shoppingSubtitle,
      onClick: () => goToTab("shopping"),
    });
    tiles.push({
      key: "photo",
      icon: "📷",
      title: "Add a photo",
      subtitle: "Work done / needs fixing",
      onClick: () => openVisit("photo"),
    });
  } else {
    tiles.push({
      key: "photo",
      icon: "📷",
      title: "Add a photo",
      subtitle: "Work done / needs fixing",
      onClick: () => openVisit("photo"),
    });
    tiles.push({
      key: "note",
      icon: "📝",
      title: "Leave a note",
      subtitle: "Running low? Broken?",
      onClick: () => openVisit("note"),
    });
  }
  // Spec §5.1: with an odd number of tiles (staff without Meals) the last one spans both columns.
  const spanLast = tiles.length % 2 === 1;

  return (
    <div className="section">
      <SunsetBand>
        <h1>Aloha, {firstName}</h1>
        <div className="where">
          {fmtShort(t0)} · 131 Galleon St, Apt 2 · Marina del Rey
        </div>
      </SunsetBand>

      {me.doorCode ? <DoorCodeBanner code={me.doorCode} /> : null}

      <div className="tiles">
        {tiles.map((tile, index) => (
          <Tile
            key={tile.key}
            icon={tile.icon}
            title={tile.title}
            subtitle={tile.subtitle}
            onClick={tile.onClick}
            span={spanLast && index === tiles.length - 1}
          />
        ))}
      </div>

      {!isOwner ? (
        <div className="row">
          <button type="button" className="linkish" onClick={() => setAccountOpen(true)}>
            Account
          </button>
        </div>
      ) : null}

      <AccountPanel open={accountOpen} onClose={() => setAccountOpen(false)} />
    </div>
  );
}
