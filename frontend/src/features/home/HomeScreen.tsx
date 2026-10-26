import { useMemo, useState } from "react";
import { fmtShort, mondayOf, today } from "../../lib/dates";
import { hoursLabel, minutesInWeek, summarise } from "../../lib/hours";
import { countJobs } from "../../lib/jobs";
import { readyCount as countReady, todayLabel } from "../../lib/visits";
import { SunsetBand } from "../../components/illustrations/SunsetBand";
import { useAccess, useApp } from "../../state/AppState";
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
  const access = useAccess();
  const isOwner = access.isOwner;
  const [accountOpen, setAccountOpen] = useState(false);
  const [housePhoto, setHousePhoto] = useState(true);
  const me = state.me;

  const t0 = today();
  const visit = state.visits[t0];

  const readyCount = useMemo(
    () => countReady(Object.values(state.tasks), state.visits, t0),
    [state.tasks, state.visits, t0],
  );

  if (!me) return null;

  const firstName = me.displayName.split(" ")[0] ?? me.displayName;
  const photoCount = visit?.photos.length ?? 0;
  const visitTitle = todayLabel(state.visits, t0);
  const visitSubtitle = `${readyCount} ${readyCount === 1 ? "task" : "tasks"} ready${
    photoCount ? ` · ${photoCount} ${photoCount === 1 ? "photo" : "photos"}` : ""
  }`;
  const counts = countJobs(state.jobs ?? []);
  // Blank until /api/state has answered; "0 open" would be a guess.
  const jobsSubtitle =
    state.jobs === null
      ? ""
      : isOwner
        ? `${counts.todo} open · ${counts.doing} in progress`
        : `${counts.todo} to do · ${counts.doing} in progress`;
  const claims = state.hours ?? [];
  const toApprove = summarise(claims).toApproveCount;
  const hoursSubtitle = state.hours === null ? "" : `${toApprove} to approve`;
  const weekSubtitle = state.hours === null ? "" : `${hoursLabel(minutesInWeek(claims, mondayOf(t0)))} this week`;

  function openVisit(intent: "photo" | "note" | null) {
    dispatch({ type: "date", date: t0 });
    goToTab("visit", intent);
  }

  const visitTile: TileSpec = {
    key: "visit", icon: "✅", title: visitTitle, subtitle: visitSubtitle, onClick: () => openVisit(null),
  };
  const jobsTile: TileSpec = {
    key: "jobs", icon: "🔨", title: "Jobs", subtitle: jobsSubtitle, onClick: () => goToTab("jobs"),
  };
  const photoTile: TileSpec = {
    key: "photo", icon: "📷", title: "Add a photo", subtitle: "Work done / needs fixing", onClick: () => openVisit("photo"),
  };

  const tiles: TileSpec[] = [];
  if (isOwner) {
    tiles.push(visitTile, jobsTile);
    tiles.push({ key: "hours", icon: "⏱️", title: "Hours", subtitle: hoursSubtitle, onClick: () => goToTab("hours") });
    tiles.push(photoTile);
  } else if (access.jobs) {
    // A builder's Home is their jobs, then the door code and Account link below.
    tiles.push(jobsTile);
  } else {
    tiles.push(visitTile, photoTile);
    tiles.push({
      key: "note",
      icon: "📝",
      title: "Leave a note",
      subtitle: "Running low? Broken?",
      onClick: () => openVisit("note"),
    });
    tiles.push({ key: "hours", icon: "⏱️", title: "Log hours", subtitle: weekSubtitle, onClick: () => goToTab("hours") });
  }
  // Spec §5.1: with an odd number of tiles (a builder's one) the last one spans both columns.
  const spanLast = tiles.length % 2 === 1;

  return (
    <div className="section">
      <SunsetBand>
        <h1>Aloha, {firstName}</h1>
        <div className="where">
          {fmtShort(t0)} · 131 Galleon St, Apt 2 · Marina del Rey
        </div>
      </SunsetBand>

      {/* house.jpg is a listing photo, kept out of the public repo: present on the server, maybe not in a dev build. */}
      {housePhoto ? (
        <img className="house-photo" src="/house.jpg" alt="131 Galleon St" onError={() => setHousePhoto(false)} />
      ) : null}

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
