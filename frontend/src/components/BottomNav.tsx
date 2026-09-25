import type { Tab } from "../state/AppState";

export interface NavItem {
  key: Tab;
  icon: string;
  label: string;
}

export function BottomNav({
  items,
  active,
  onSelect,
}: {
  items: NavItem[];
  active: Tab;
  onSelect: (tab: Tab) => void;
}) {
  return (
    <nav className="bottom-nav" aria-label="Main">
      {items.map((item) => (
        <button
          key={item.key}
          type="button"
          className="nav-item"
          aria-current={active === item.key ? "page" : undefined}
          onClick={() => onSelect(item.key)}
        >
          <span className="nav-icon" aria-hidden="true">
            {item.icon}
          </span>
          <span className="nav-label">{item.label}</span>
        </button>
      ))}
    </nav>
  );
}
