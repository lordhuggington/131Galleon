import { Button } from "../../components/Button";
import { Dialog } from "../../components/Dialog";
import { prettyPhone } from "../../lib/phone";
import { useApp } from "../../state/AppState";

export function AccountPanel({ open, onClose }: { open: boolean; onClose: () => void }) {
  const { state, signOut } = useApp();
  const me = state.me;

  return (
    <Dialog open={open} onClose={onClose} labelledBy="accountTitle">
      {me ? (
        <div className="login">
          <h3 id="accountTitle">Account</h3>
          <div>
            <div className="t">{me.displayName}</div>
            <div className="small muted">{me.label || "Staff"}</div>
          </div>
          <div>
            <div className="label">Phone</div>
            <div className="mono">{me.phone ? prettyPhone(me.phone) : "No phone on file yet"}</div>
            <div className="small muted">Ask Owen to change this.</div>
          </div>
          <div className="row">
            <Button variant="primary" onClick={() => void signOut()}>
              Sign out
            </Button>
            <Button variant="ghost" onClick={onClose}>
              Close
            </Button>
          </div>
        </div>
      ) : null}
    </Dialog>
  );
}
