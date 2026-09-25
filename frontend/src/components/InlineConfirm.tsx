import { Button } from "./Button";

export function InlineConfirm({
  question,
  confirmLabel,
  cancelLabel = "Keep",
  onConfirm,
  onCancel,
}: {
  question?: string;
  confirmLabel: string;
  cancelLabel?: string;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  return (
    <span className="row">
      {question ? <span className="small">{question}</span> : null}
      <Button variant="danger" onClick={onConfirm}>
        {confirmLabel}
      </Button>
      <Button variant="ghost" onClick={onCancel}>
        {cancelLabel}
      </Button>
    </span>
  );
}
