export function Tile({
  icon,
  title,
  subtitle,
  onClick,
  span = false,
}: {
  icon: string;
  title: string;
  subtitle: string;
  onClick: () => void;
  span?: boolean;
}) {
  return (
    <button type="button" className={span ? "tile span2" : "tile"} onClick={onClick}>
      <span className="tile-icon" aria-hidden="true">
        {icon}
      </span>
      <span className="tile-title">{title}</span>
      <span className="tile-sub">{subtitle}</span>
    </button>
  );
}
