import { useState } from "react";
import { uploadPhoto } from "../../api/client";
import type { PhotoKind } from "../../api/types";
import { Chips } from "../../components/Chips";
import { PhotoUploadForm } from "../../components/PhotoUploadForm";

export function PhotoUploader({ date, onDone }: { date: string; onDone: () => void }) {
  const [kind, setKind] = useState<PhotoKind>("done");

  return (
    <PhotoUploadForm
      idPrefix="photo"
      send={(jpeg, caption) => uploadPhoto(date, jpeg, kind, caption)}
      onDone={onDone}
      captionPlaceholder="e.g. Spare room blind is sticking"
    >
      <Chips<PhotoKind>
        label="What kind of photo"
        value={kind}
        onChange={setKind}
        options={[
          { value: "done", label: "Work completed" },
          { value: "fix", label: "Needs attention" },
        ]}
      />
    </PhotoUploadForm>
  );
}
