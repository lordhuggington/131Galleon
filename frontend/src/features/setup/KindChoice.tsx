import type { Kind } from "../../api/types";
import { Chips } from "../../components/Chips";

export const KIND_LABEL: Record<Kind, string> = { housekeeper: "Housekeeper", builder: "Builder" };

/** Staff only: which run sheet they get — the Visit and Hours, or Jobs. */
export function KindChoice({ value, onChange }: { value: Kind; onChange: (kind: Kind) => void }) {
  return (
    <div className="field span-all">
      <span>Works as</span>
      <Chips<Kind>
        label="Works as"
        value={value}
        onChange={onChange}
        options={[
          { value: "housekeeper", label: KIND_LABEL.housekeeper },
          { value: "builder", label: KIND_LABEL.builder },
        ]}
      />
      <small>{value === "builder" ? "Sees Jobs only." : "Sees the Visit run sheet and claims hours."}</small>
    </div>
  );
}
