import { useEffect, useState } from "react";
import type { Photo } from "../../api/types";
import { Button } from "../../components/Button";
import { Card } from "../../components/Card";
import { Dialog } from "../../components/Dialog";
import { InlineConfirm } from "../../components/InlineConfirm";
import { Pill } from "../../components/Pill";
import { fmtStamp } from "../../lib/dates";
import { useApp } from "../../state/AppState";
import { PhotoUploader } from "./PhotoUploader";

const KIND_LABEL: Record<Photo["kind"], string> = { done: "Work completed", fix: "Needs attention" };

export function PhotosCard({
  date,
  photos,
  openUploaderOnMount,
}: {
  date: string;
  photos: Photo[];
  openUploaderOnMount: boolean;
}) {
  const { state, dispatch, mutate } = useApp();
  const me = state.me;
  const [showUploader, setShowUploader] = useState(false);
  const [lightbox, setLightbox] = useState<Photo | null>(null);

  // The Home "Add a photo" tile opens the uploader panel and focuses its file input.
  // A synthetic click on the input would be blocked outside a user gesture, so we do not try.
  useEffect(() => {
    if (openUploaderOnMount) setShowUploader(true);
  }, [openUploaderOnMount]);

  function canDelete(photo: Photo): boolean {
    return me !== null && (me.role === "owner" || photo.by.id === me.id);
  }

  function remove(photo: Photo) {
    dispatch({ type: "remove-photo", date, photoId: photo.id });
    dispatch({ type: "confirm", key: null });
    void mutate("DELETE", `/api/visits/${date}/photos/${photo.id}`, undefined, "Photo deleted").catch(() => {});
  }

  return (
    <Card>
      <div className="row">
        <h3>Photos</h3>
        <span className="spacer" />
        <span className="mono small muted">{photos.length}</span>
      </div>

      {photos.length === 0 ? (
        <p className="small muted mt8">No photos for this visit yet.</p>
      ) : (
        <div className="photos">
          {photos.map((photo) => (
            <div className="photo" key={photo.id}>
              <button
                type="button"
                className="photo-thumb"
                aria-label={`Open photo: ${photo.caption || KIND_LABEL[photo.kind]}`}
                onClick={() => setLightbox(photo)}
              >
                <img src={photo.url} alt={photo.caption || KIND_LABEL[photo.kind]} loading="lazy" />
              </button>
              <div className="row">
                <Pill variant={photo.kind === "done" ? "ok" : "warn"}>{KIND_LABEL[photo.kind]}</Pill>
                {canDelete(photo) ? (
                  state.ui.confirm === `photo:${photo.id}` ? (
                    <InlineConfirm
                      confirmLabel="Delete"
                      onConfirm={() => remove(photo)}
                      onCancel={() => dispatch({ type: "confirm", key: null })}
                    />
                  ) : (
                    <button
                      type="button"
                      className="linkish"
                      onClick={() => dispatch({ type: "confirm", key: `photo:${photo.id}` })}
                    >
                      Delete
                    </button>
                  )
                ) : null}
              </div>
              {photo.caption ? <div className="photo-cap">{photo.caption}</div> : null}
            </div>
          ))}
        </div>
      )}

      {showUploader ? (
        <PhotoUploader date={date} onDone={() => setShowUploader(false)} />
      ) : (
        <div className="row mt12">
          <Button variant="primary" onClick={() => setShowUploader(true)}>
            Add photo
          </Button>
        </div>
      )}

      <Dialog open={lightbox !== null} onClose={() => setLightbox(null)} labelledBy="photoTitle">
        {lightbox ? (
          <div className="login">
            <h3 id="photoTitle">{KIND_LABEL[lightbox.kind]}</h3>
            <img src={lightbox.url} alt={lightbox.caption || KIND_LABEL[lightbox.kind]} />
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
    </Card>
  );
}
