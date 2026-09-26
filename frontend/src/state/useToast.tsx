import { createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from "react";
import { Toast } from "../components/Toast";

interface ToastValue {
  toast: (message: string) => void;
}

const ToastContext = createContext<ToastValue>({ toast: () => {} });

export function useToast(): ToastValue {
  return useContext(ToastContext);
}

export function ToastProvider({ children }: { children: ReactNode }) {
  const [message, setMessage] = useState<string | null>(null);
  const timer = useRef<number | undefined>(undefined);

  const toast = useCallback((next: string) => {
    setMessage(next);
    window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => setMessage(null), 2600);
  }, []);

  useEffect(() => () => window.clearTimeout(timer.current), []);

  return (
    <ToastContext.Provider value={{ toast }}>
      {children}
      <Toast message={message} />
    </ToastContext.Provider>
  );
}
