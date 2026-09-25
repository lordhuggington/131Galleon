import { useEffect, useRef } from "react";
import { Button } from "../../components/Button";
import { Card } from "../../components/Card";
import { Check } from "../../components/Check";
import { Chips } from "../../components/Chips";
import { Pill } from "../../components/Pill";
import { filterItems, groupByAisle, shoppingText, type ShopFilter } from "../../lib/shopping";
import { useApp } from "../../state/AppState";
import { useToast } from "../../state/useToast";
import { WeekNav } from "../meals/WeekNav";

export function ShoppingScreen() {
  const { state, dispatch, mutate, goToTab } = useApp();
  const { toast } = useToast();
  const week = state.ui.week;
  const filter = state.ui.shopFilter;
  const copyText = state.ui.copyText;
  const plan = state.plans[week] ?? null;
  const loaded = state.planLoaded[week] === true;
  const box = useRef<HTMLTextAreaElement | null>(null);

  useEffect(() => {
    if (copyText !== null) {
      box.current?.focus();
      box.current?.select();
    }
  }, [copyText]);

  const got = plan?.got ?? {};
  const items = filterItems(plan?.shopping ?? [], filter);
  const fresh = items.filter((i) => !i.stock);
  const stock = items.filter((i) => i.stock);
  const left = fresh.filter((i) => !got[i.id]).length;

  function toggleGot(itemId: string, next: boolean) {
    dispatch({ type: "got", week, itemId, got: next });
    void mutate("PATCH", `/api/plans/${week}/shopping/${encodeURIComponent(itemId)}`, { got: next }).catch(() => {});
  }

  async function copy() {
    if (!plan) return;
    const text = shoppingText(week, plan.shopping ?? [], got, filter);
    try {
      await navigator.clipboard.writeText(text);
      dispatch({ type: "copy-text", text: null });
      toast("Shopping list copied");
    } catch {
      // Clipboard blocked (no permission, or not a secure context): show the text to select.
      dispatch({ type: "copy-text", text });
    }
  }

  if (!loaded) {
    return (
      <div className="section">
        <WeekNav />
        <div className="empty">Loading…</div>
      </div>
    );
  }

  if (!plan) {
    return (
      <div className="section">
        <WeekNav />
        <Card className="empty">
          No menu for this week yet. Create one on the{" "}
          <button type="button" className="linkish" onClick={() => goToTab("meals")}>
            Meals
          </button>{" "}
          tab.
        </Card>
      </div>
    );
  }

  const row = (item: { id: string; item: string; buy: string; for: "both" | "tue" | "fri" }) => (
    <div className={got[item.id] ? "shop-item done" : "shop-item"} key={item.id}>
      <Check checked={Boolean(got[item.id])} label={item.item} onClick={() => toggleGot(item.id, !got[item.id])} />
      <div className="t">
        {item.item}
        {item.for === "both" ? null : <> <Pill>{item.for === "tue" ? "Tue" : "Fri"}</Pill></>}
      </div>
      <div className="b">{item.buy}</div>
    </div>
  );

  return (
    <div className="section">
      <WeekNav />

      <div className="row">
        <Chips<ShopFilter>
          label="Which order"
          value={filter}
          onChange={(next) => dispatch({ type: "shop-filter", filter: next })}
          options={[
            { value: "all", label: "Everything" },
            { value: "tue", label: "Deliver by Tue" },
            { value: "fri", label: "Friday-only" },
          ]}
        />
        <span className="spacer" />
        <Button onClick={() => void copy()}>Copy list</Button>
      </div>

      <p className="small muted">
        One order before Tuesday covers the whole week (buy the Friday meat and freeze it if the use-by is tight). Or
        split: "Deliver by Tue" first, then the Friday-only items. <span className="mono">{left}</span> fresh items left
        to order.
      </p>

      {copyText !== null ? (
        <Card>
          <label className="field" htmlFor="copyBox">
            <span>Copying was blocked. Select all and copy this instead.</span>
            <textarea id="copyBox" ref={box} className="copybox" readOnly value={copyText} />
          </label>
        </Card>
      ) : null}

      {groupByAisle(fresh).map(([aisle, list]) => (
        <div className="group" key={aisle}>
          <div className="group-h">
            <span className="label">{aisle}</span>
            <span className="mono small muted">
              {list.filter((i) => got[i.id]).length}/{list.length}
            </span>
          </div>
          {list.map(row)}
        </div>
      ))}

      {stock.length > 0 ? (
        <div className="group">
          <div className="group-h">
            <span className="label">Pantry basics: only if you've run out</span>
            <span className="mono small muted">{stock.length}</span>
          </div>
          {stock.map(row)}
        </div>
      ) : null}

      {plan.leftovers.length > 0 ? (
        <Card>
          <div className="label">Expected leftovers</div>
          <ul className="steps mt8">
            {plan.leftovers.map((item, index) => (
              <li key={index}>{item}</li>
            ))}
          </ul>
          <p className="small muted mt8">Next week's menu is planned to use these up first.</p>
        </Card>
      ) : null}
    </div>
  );
}
