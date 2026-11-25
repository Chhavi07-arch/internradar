import type { PDFDocumentLoadingTask, PDFWorker } from "pdfjs-dist";
import { RESUME_LIMITS as limits } from "./resume";

export async function readResumeFile(file: File, signal: AbortSignal): Promise<string> {
  const isPdf = /\.pdf$/i.test(file.name);
  if (!isPdf && !/\.txt$/i.test(file.name)) throw new Error("Choose a PDF or plain-text (.txt) file.");
  if (file.size > limits.bytes) throw new Error("Choose a file no larger than 5 MB.");
  if (!file.size) throw new Error("That file is empty.");
  signal.throwIfAborted();

  let nativeWorker: Worker | undefined;
  let pdfWorker: PDFWorker | undefined;
  let task: PDFDocumentLoadingTask | undefined;
  let closed = false;
  let destroying: Promise<void> | undefined;
  let rejectStop: (error: unknown) => void;
  const stopped = new Promise<never>((_, reject) => { rejectStop = reject; });
  const fail = (error: Error) => rejectStop(error);
  const abort = () => fail(new DOMException("Resume extraction cancelled.", "AbortError"));
  const timer = window.setTimeout(() => fail(new Error("This file took too long to read. Try pasting text instead.")), limits.ms);
  signal.addEventListener("abort", abort, { once: true });
  const encrypted = "Encrypted PDFs aren’t supported. Paste the text or enter your details manually.";
  const unreadable = "This PDF could not be read. Try pasting text or filling in the profile manually.";

  async function read() {
    if (!isPdf) {
      const text = await file.text();
      if (text.includes("\0") || text.includes("�")) throw new Error("Choose a UTF-8 plain-text file, or paste the text instead.");
      return text;
    }
    const [pdfjs, { default: workerUrl }, bytes] = await Promise.all([
      import("pdfjs-dist"), import("pdfjs-dist/build/pdf.worker.min.mjs?url"), file.arrayBuffer(),
    ]).catch(() => { throw new Error(unreadable); });
    if (closed) throw new DOMException("Resume extraction cancelled.", "AbortError");
    const data = new Uint8Array(bytes);
    if (!new TextDecoder().decode(data.subarray(0, 1024)).includes("%PDF-")) throw new Error(unreadable);

    // A dedicated, same-origin worker lets us force-stop malformed or expensive files.
    nativeWorker = new Worker(workerUrl, { type: "module" });
    nativeWorker.addEventListener("error", () => fail(new Error(unreadable)), { once: true });
    pdfWorker = pdfjs.PDFWorker.create({ port: nativeWorker, verbosity: 0 });
    task = pdfjs.getDocument({
      data, worker: pdfWorker, verbosity: 0, disableWorkerRendering: true, disableFontFace: true,
      useSystemFonts: false, useWorkerFetch: false, useWasm: false, stopAtErrors: true,
    });
    task.onPassword = () => fail(new Error(encrypted));
    const document = await task.promise.catch(() => { throw new Error(unreadable); });
    if (document.numPages > limits.pages) throw new Error("Choose a PDF with 10 pages or fewer.");
    const { info } = await document.getMetadata().catch(() => { throw new Error(unreadable); });
    if ((info as { EncryptFilterName?: string }).EncryptFilterName) throw new Error(encrypted);
    const fragments: string[] = [];
    let length = 0;
    for (let i = 1; i <= document.numPages; i++) {
      const page = await document.getPage(i).catch(() => { throw new Error(unreadable); });
      const reader = page.streamTextContent().getReader();
      try {
        while (true) {
          const { value, done } = await reader.read().catch(() => { throw new Error(unreadable); });
          if (done) break;
          for (const item of value.items) {
            if (!("str" in item)) continue;
            length += item.str.length + 1;
            if (length > limits.chars) throw new Error("Resume text is too long (50,000 character limit).");
            fragments.push(item.str + (item.hasEOL ? "\n" : " "));
          }
        }
      } finally {
        reader.releaseLock();
      }
      fragments.push("\n");
    }
    return fragments.join("");
  }

  try {
    const text = await Promise.race([read(), stopped]);
    if (text.length > limits.chars) throw new Error("Resume text is too long (50,000 character limit).");
    if (!text.trim()) throw new Error(isPdf ? "No selectable text found. Scanned PDFs need OCR; paste text or enter details manually." : "No text found in that file.");
    // On normal completion allow PDF.js to gracefully close the loading task.
    destroying = task?.destroy();
    await Promise.race([destroying, stopped]);
    return text;
  } finally {
    closed = true;
    window.clearTimeout(timer);
    signal.removeEventListener("abort", abort);
    // On error/cancel/timeout initiate PDF.js cleanup, then terminate without waiting
    // for an unresponsive worker to acknowledge its teardown message.
    (destroying ?? task?.destroy())?.catch(() => undefined);
    pdfWorker?.destroy();
    nativeWorker?.terminate();
  }
}
