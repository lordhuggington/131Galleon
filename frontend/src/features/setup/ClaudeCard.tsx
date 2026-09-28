import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../../api/client";
import type { Connection, ConnectionsResponse, OkResponse } from "../../api/types";
import { Button } from "../../components/Button";
import { Card } from "../../components/Card";
import { fmtStamp } from "../../lib/dates";
import { useIsOwner } from "../../state/AppState";
import { useToast } from "../../state/useToast";

/**
 * Setup → Claude: the connector URL to paste into the Claude app, and the OAuth grants
 * that came back. Owner-only, and deliberately outside the 20 s polling loop — a
 * connection is made about once and then left alone.
 */
export function ClaudeCard() {
  const isOwner = useIsOwner();
  const { toast } = useToast();
  const [mcpUrl, setMcpUrl] = useState("");
  const [connections, setConnections] = useState<Connection[] | null>(null);
  const [error, setError] = useState("");
  // Local state, not ui.copyText: that one belongs to the shopping list and is cleared
  // on every week, filter and tab change.
  const [copyText, setCopyText] = useState<string | null>(null);
  const box = useRef<HTMLTextAreaElement | null>(null);
  // Activity tears this screen's effects down when the tab is hidden; a late response
  // must not write into a card that is no longer on screen.
  const alive = useRef(true);

  const load = useCallback(async () => {
    try {
      const data = await api<ConnectionsResponse>("GET", "/api/oauth/connections");
      if (!alive.current) return;
      setMcpUrl(data.mcpUrl);
      setConnections(data.connections);
      setError("");
    } catch (e) {
      if (!alive.current) return;
      setConnections([]);
      setError(e instanceof Error ? e.message : "Couldn't load the Claude connections.");
    }
  }, []);

  useEffect(() => {
    alive.current = true;
    if (isOwner) void load();
    return () => {
      alive.current = false;
    };
  }, [isOwner, load]);

  useEffect(() => {
    if (copyText !== null) {
      box.current?.focus();
      box.current?.select();
    }
  }, [copyText]);

  if (!isOwner) return null;

  async function copy() {
    if (!mcpUrl) return;
    try {
      await navigator.clipboard.writeText(mcpUrl);
      setCopyText(null);
      toast("Connector URL copied");
    } catch {
      // Clipboard blocked (no permission, or not a secure context): show the text to select.
      setCopyText(mcpUrl);
    }
  }

  // No confirm step: reconnecting is one click in Claude.
  async function disconnect(family: string) {
    try {
      await api<OkResponse>("DELETE", `/api/oauth/connections/${encodeURIComponent(family)}`);
      setError("");
      toast("Disconnected");
    } catch (e) {
      setError(e instanceof Error ? e.message : "Couldn't disconnect that.");
    }
    await load();
  }

  return (
    <Card>
      <h3 className="h19">Claude</h3>
      <p className="small muted mt6">
        Add Galleon as a connector in Claude, then plan a prep session by chatting. In Claude: Settings → Connectors →
        Add custom connector. Paste this URL, then sign in as an owner when Claude asks.
      </p>

      <div className="mcp-url">
        <span className="mono">{mcpUrl || "Loading…"}</span>
        <span className="spacer" />
        <Button onClick={() => void copy()} disabled={!mcpUrl}>
          Copy
        </Button>
      </div>

      {copyText !== null ? (
        <label className="field mt12" htmlFor="mcpUrlBox">
          <span>Copying was blocked. Select all and copy this instead.</span>
          <textarea id="mcpUrlBox" ref={box} className="mono" readOnly value={copyText} />
        </label>
      ) : null}

      {error ? (
        <div className="err mt12" role="alert">
          {error}
        </div>
      ) : null}

      <div className="group mt12">
        <div className="group-h">
          <span className="label">Connections</span>
          <span className="mono small muted">{connections?.length ?? 0}</span>
        </div>
        {connections === null ? (
          <div className="conn small muted">Loading…</div>
        ) : connections.length === 0 ? (
          <div className="conn small muted">Not connected yet.</div>
        ) : (
          connections.map((connection) => (
            <div className="conn" key={connection.family}>
              <div>
                <div className="t">{connection.clientName}</div>
                <div className="small muted">
                  connected {fmtStamp(connection.connectedAt)} ·{" "}
                  {connection.lastUsedAt === null
                    ? "not used yet"
                    : `last used ${fmtStamp(connection.lastUsedAt)}`}
                </div>
              </div>
              <Button variant="danger" onClick={() => void disconnect(connection.family)}>
                Disconnect
              </Button>
            </div>
          ))
        )}
      </div>
    </Card>
  );
}
