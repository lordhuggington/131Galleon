import { useState } from "react";
import { uploadJobPhoto } from "../../api/client";
import type { Job, JobPhoto } from "../../api/types";
import { Button } from "../../components/Button";
import { Dialog } from "../../components/Dialog";
import { InlineConfirm } from "../../components/InlineConfirm";
import { PhotoUploadForm } from "../../components/PhotoUploadForm";
import { fmtStamp } from "../../lib/dates";
import { useApp } from "../../state/AppState";

export function JobPhotos({ job }: { job: Job }) {
  const { state, dispatch, mutate } = useApp();
  const me = state.me;
  const [showUploader, setShowUploader] = useState(false);
  const [lightbox, setLightbox] = useState<JobPhoto | null>(null);

  function canDelete(photo: JobPhoto): boolean {
    return me !== null && (me.role === "owner" || photo.by.id === me.id);
  }

  function remove(photo: JobPhoto) {
    dispatch({ type: "remove-job-photo", jobId: job.id, photoId: photo.id });
    void mutate("DELETE", `/api/jobs/${job.id}/photos/${photo.id}`, undefined, "Photo deleted").catch(() => {});
  }

  const alt = (photo: JobPhoto) => photo.caption || `Photo for ${job.title}`;

  return (
    <div>
      <div className="row">
        <span className="label">Photos</span>
        <span className="mono small muted">{job.photos.length}</span>
      </div>

      {job.photos.length > 0 ? (
        <div className="photos">
          {job.photos.map((photo) => (
            <div className="photo" key={photo.id}>
              <button
                type="button"
                className="photo-thumb"
                aria-label={`Open photo: ${alt(photo)}`}
                onClick={() => setLightbox(photo)}
              >
                <img src={photo.url} alt={alt(photo)} loading="lazy" />
              </button>
              {photo.caption ? <div className="photo-cap">{photo.caption}</div> : null}
              {canDelete(photo) ? (
                <div className="row">
                  {state.ui.confirm === `jp:${photo.id}` ? (
                    <InlineConfirm
                      confirmLabel="Delete"
                      onConfirm={() => remove(photo)}
                      onCancel={() => dispatch({ type: "confirm", key: null })}
                    />
                  ) : (
                    <button
                      type="button"
                      className="linkish"
                      onClick={() => dispatch({ type: "confirm", key: `jp:${photo.id}` })}
                    >
                      Delete
                    </button>
                  )}
                </div>
              ) : null}
            </div>
          ))}
        </div>
      ) : null}

      {showUploader ? (
        <PhotoUploadForm
          idPrefix={`jobPhoto${job.id}`}
          send={(jpeg, caption) => uploadJobPhoto(job.id, jpeg, caption)}
          onDone={() => setShowUploader(false)}
          captionPlaceholder="e.g. Before: the crack above the door"
        />
      ) : (
        <div className="row mt8">
          <Button onClick={() => setShowUploader(true)}>Add photo</Button>
        </div>
      )}

      <Dialog open={lightbox !== null} onClose={() => setLightbox(null)} labelledBy={`jobPhotoTitle${job.id}`}>
        {lightbox ? (
          <div className="login">
            <h3 id={`jobPhotoTitle${job.id}`}>{job.title}</h3>
            <img src={lightbox.url} alt={alt(lightbox)} />
            {lightbox.caption ? <p className="small">{lightbox.caption}</p> : null}
            <p className="small muted">
              {lightbox.by.displayName} · {fmtStamp(lightbox.createdAt)}
            </p>
            <div className="row">
              <Button onClick={() => setLightbox(null)}>Close</Button>
            </div>
          </div>
        ) : null}
      </Dialog>
    </div>
  );
}
