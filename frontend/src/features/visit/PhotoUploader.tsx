import { useActionState, useEffect, useRef, useState } from "react";
import { uploadPhoto } from "../../api/client";
import type { PhotoKind } from "../../api/types";
import { Button } from "../../components/Button";
import { Chips } from "../../components/Chips";
import { Field } from "../../components/Field";
import { resizeToJpeg } from "../../lib/image";
import { useApp } from "../../state/AppState";
import { useToast } from "../../state/useToast";

const UNSUPPORTED = "That photo format isn't supported here — take a screenshot of it or use your phone.";

export function PhotoUploader({ date, onDone }: { date: string; onDone: () => void }) {
  const { refresh } = useApp();
  const { toast } = useToast();
  const input = useRef<HTMLInputElement | null>(null);
  const [files, setFiles] = useState<File[]>([]);
  const [previews, setPreviews] = useState<string[]>([]);
  const [kind, setKind] = useState<PhotoKind>("done");
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
      setProgress("");
      await refresh();
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
        await uploadPhoto(date, jpeg, kind, caption.trim());
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
      <Field label="Photo" htmlFor="photoFile" hint="Your phone will offer the camera or your library.">
        {/* No capture attribute: let the OS choose (spec §5.2). */}
        <input
          id="photoFile"
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

      <Chips<PhotoKind>
        label="What kind of photo"
        value={kind}
        onChange={setKind}
        options={[
          { value: "done", label: "Work completed" },
          { value: "fix", label: "Needs attention" },
        ]}
      />

      <Field label="Caption (optional)" htmlFor="photoCaption">
        <input
          id="photoCaption"
          type="text"
          maxLength={300}
          value={caption}
          onChange={(e) => setCaption(e.target.value)}
          placeholder="e.g. Spare room blind is sticking"
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
