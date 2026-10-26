import { useActionState, useEffect, useRef, useState, type ReactNode } from "react";
import { resizeToJpeg } from "../lib/image";
import { useApp } from "../state/AppState";
import { useToast } from "../state/useToast";
import { Button } from "./Button";
import { Field } from "./Field";

const UNSUPPORTED = "That photo format isn't supported here — take a screenshot of it or use your phone.";

/**
 * Pick one or more photos, resize each to JPEG in the browser and hand it to `send`.
 * Shared by visit photos and job photos; `children` sits between the previews and the caption
 * (the visit uploader puts its "what kind of photo" chips there).
 */
export function PhotoUploadForm({
  idPrefix,
  send,
  onDone,
  captionPlaceholder,
  children,
}: {
  /** Keeps input ids unique while several screens are mounted under <Activity>. */
  idPrefix: string;
  send: (jpeg: Blob, caption: string) => Promise<unknown>;
  onDone: () => void;
  captionPlaceholder: string;
  children?: ReactNode;
}) {
  const { refresh } = useApp();
  const { toast } = useToast();
  const input = useRef<HTMLInputElement | null>(null);
  const [files, setFiles] = useState<File[]>([]);
  const [previews, setPreviews] = useState<string[]>([]);
  const [caption, setCaption] = useState("");
  const [progress, setProgress] = useState("");

  // Object URLs are revoked when the selection changes or the uploader closes.
  useEffect(() => {
    const urls = files.map((file) => URL.createObjectURL(file));
    setPreviews(urls);
    return () => {
      for (const url of urls) URL.revokeObjectURL(url);
    };
  }, [files]);

  useEffect(() => {
    input.current?.focus();
  }, []);

  const [error, upload, uploading] = useActionState<string | null, FormData>(async () => {
    if (files.length === 0) return "Choose a photo first.";
    const chosen = files;
    let sent = 0;
    let undecodable = 0;
    const finish = async (): Promise<void> => {
      // Progress stays up through the resync so the panel is never silent with Upload disabled.
      await refresh();
      setProgress("");
      if (sent > 0) toast(sent === 1 ? "Photo added" : `${sent} photos added`);
    };
    for (const [index, file] of chosen.entries()) {
      setProgress(`Uploading ${index + 1} of ${chosen.length}…`);
      let jpeg: Blob;
      try {
        jpeg = await resizeToJpeg(file);
      } catch {
        undecodable += 1;
        continue;
      }
      try {
        await send(jpeg, caption.trim());
        sent += 1;
      } catch (e) {
        // Keep what hasn't been sent so a retry doesn't upload duplicates.
        setFiles(chosen.slice(index));
        await finish();
        return e instanceof Error ? e.message : "That photo didn't upload.";
      }
    }
    await finish();
    if (undecodable > 0) {
      setFiles([]);
      return UNSUPPORTED;
    }
    setFiles([]);
    setCaption("");
    onDone();
    return null;
  }, null);

  return (
    <form className="uploader" action={upload}>
      <Field label="Photo" htmlFor={`${idPrefix}File`} hint="Your phone will offer the camera or your library.">
        {/* No capture attribute: let the OS choose (spec §5.2). */}
        <input
          id={`${idPrefix}File`}
          ref={input}
          type="file"
          accept="image/*"
          multiple
          onChange={(e) => setFiles(Array.from(e.target.files ?? []))}
        />
      </Field>

      {previews.length > 0 ? (
        <div className="preview-strip">
          {previews.map((url, index) => (
            <img key={url} src={url} alt={`Selected photo ${index + 1}`} />
          ))}
        </div>
      ) : null}

      {children}

      <Field label="Caption (optional)" htmlFor={`${idPrefix}Caption`}>
        <input
          id={`${idPrefix}Caption`}
          type="text"
          maxLength={300}
          value={caption}
          onChange={(e) => setCaption(e.target.value)}
          placeholder={captionPlaceholder}
        />
      </Field>

      <div className="row">
        <Button type="submit" variant="primary" disabled={uploading || files.length === 0}>
          Upload
        </Button>
        <Button variant="ghost" disabled={uploading} onClick={onDone}>
          Cancel
        </Button>
        {progress ? <span className="small muted">{progress}</span> : null}
      </div>

      {error ? (
        <div className="err" role="alert">
          {error}
        </div>
      ) : null}
    </form>
  );
}
