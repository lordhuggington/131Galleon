export function Toast({ message }: { message: string | null }) {
  return (
    <div className="toast" role="status" aria-live="polite" hidden={message === null}>
      {message}
    </div>
  );
}
