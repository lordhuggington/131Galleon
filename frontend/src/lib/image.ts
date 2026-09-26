// Resize a picked photo to a JPEG in the browser so the server only ever stores JPEG.

export class ImageDecodeError extends Error {
  constructor(message = "Could not decode that image") {
    super(message);
    this.name = "ImageDecodeError";
  }
}

async function decode(file: File): Promise<ImageBitmap | HTMLImageElement> {
  if (typeof createImageBitmap === "function") {
    try {
      // "from-image" honours the EXIF orientation iPhones set.
      return await createImageBitmap(file, { imageOrientation: "from-image" });
    } catch {
      // Fall through to the <img> path (older Safari, unsupported option).
    }
  }
  const url = URL.createObjectURL(file);
  try {
    return await new Promise<HTMLImageElement>((resolve, reject) => {
      const img = new Image();
      img.onload = () => resolve(img);
      img.onerror = () => reject(new ImageDecodeError(`Could not decode ${file.name}`));
      img.src = url;
    });
  } finally {
    URL.revokeObjectURL(url);
  }
}

/**
 * Scales the longest edge down to maxEdge and re-encodes as JPEG.
 * Throws ImageDecodeError when the browser cannot decode the file (e.g. HEIC outside Safari).
 */
export async function resizeToJpeg(file: File, maxEdge = 1600, quality = 0.85): Promise<Blob> {
  const source = await decode(file);
  const width = source.width;
  const height = source.height;
  if (!width || !height) throw new ImageDecodeError(`Could not read the size of ${file.name}`);
  const scale = Math.min(1, maxEdge / Math.max(width, height));
  const canvas = document.createElement("canvas");
  canvas.width = Math.max(1, Math.round(width * scale));
  canvas.height = Math.max(1, Math.round(height * scale));
  const ctx = canvas.getContext("2d");
  if (!ctx) throw new ImageDecodeError("This browser can't draw to a canvas");
  ctx.drawImage(source, 0, 0, canvas.width, canvas.height);
  if ("close" in source) source.close();
  return await new Promise<Blob>((resolve, reject) => {
    canvas.toBlob(
      (blob) => (blob ? resolve(blob) : reject(new ImageDecodeError("Could not encode the photo as JPEG"))),
      "image/jpeg",
      quality,
    );
  });
}
