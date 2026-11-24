import { unzipSync } from 'fflate';

export const MAX_BULK_PDFS = 50;
const MAX_UNZIPPED_BYTES = 500 * 1024 * 1024; // 500 MB safety cap

export interface PdfIntakeResult {
  files: File[];
  skippedCount: number;
  warnings: string[];
}

interface FileSystemEntryLike {
  isFile: boolean;
  isDirectory: boolean;
  name: string;
}

interface FileSystemFileEntryLike extends FileSystemEntryLike {
  isFile: true;
  file: (
    successCallback: (file: File) => void,
    errorCallback?: (err: DOMException) => void,
  ) => void;
}

interface FileSystemDirectoryReaderLike {
  readEntries: (
    successCallback: (entries: FileSystemEntryLike[]) => void,
    errorCallback?: (err: DOMException) => void,
  ) => void;
}

interface FileSystemDirectoryEntryLike extends FileSystemEntryLike {
  isDirectory: true;
  createReader: () => FileSystemDirectoryReaderLike;
}

function isIgnoredEntryName(path: string): boolean {
  const segments = path.split('/').filter(Boolean);
  if (segments.some((seg) => seg === '__MACOSX' || seg.startsWith('.'))) {
    return true;
  }
  const base = segments[segments.length - 1] ?? '';
  return base.startsWith('._') || base === '.DS_Store';
}

function isPdfFilename(name: string): boolean {
  return name.toLowerCase().endsWith('.pdf');
}

function isZipFilename(name: string): boolean {
  return name.toLowerCase().endsWith('.zip');
}

function readAllDirectoryEntries(
  reader: FileSystemDirectoryReaderLike,
): Promise<FileSystemEntryLike[]> {
  return new Promise((resolve, reject) => {
    const entries: FileSystemEntryLike[] = [];
    const readBatch = () => {
      reader.readEntries(
        (batch) => {
          if (batch.length === 0) {
            resolve(entries);
          } else {
            entries.push(...batch);
            readBatch();
          }
        },
        (err) => reject(err),
      );
    };
    readBatch();
  });
}

function readFileEntry(entry: FileSystemFileEntryLike): Promise<File> {
  return new Promise((resolve, reject) => {
    entry.file(resolve, reject);
  });
}

async function collectFromEntry(
  entry: FileSystemEntryLike,
  out: File[],
): Promise<void> {
  if (isIgnoredEntryName(entry.name)) return;

  if (entry.isFile) {
    const file = await readFileEntry(entry as FileSystemFileEntryLike);
    out.push(file);
    return;
  }

  if (entry.isDirectory) {
    const dir = entry as FileSystemDirectoryEntryLike;
    const children = await readAllDirectoryEntries(dir.createReader());
    for (const child of children) {
      await collectFromEntry(child, out);
    }
  }
}

async function unpackZipFile(
  zipFile: File,
  budget: { remainingBytes: number },
  warnings: string[],
): Promise<{ pdfs: File[]; skipped: number }> {
  const pdfs: File[] = [];
  let skipped = 0;

  try {
    const buffer = new Uint8Array(await zipFile.arrayBuffer());
    let limitExceeded = false;

    const entries = unzipSync(buffer, {
      filter(info) {
        if (info.name.endsWith('/') || isIgnoredEntryName(info.name)) {
          return false;
        }
        if (!isPdfFilename(info.name)) {
          skipped += 1;
          return false;
        }
        if (info.originalSize > budget.remainingBytes) {
          limitExceeded = true;
          return false;
        }
        budget.remainingBytes -= info.originalSize;
        return true;
      },
    });

    for (const [entryPath, bytes] of Object.entries(entries)) {
      const basename = entryPath.split('/').pop() || entryPath;
      const copy = new Uint8Array(bytes.byteLength);
      copy.set(bytes);
      pdfs.push(new File([copy.buffer], basename, { type: 'application/pdf' }));
    }

    if (limitExceeded) {
      warnings.push(
        `Stopped extracting "${zipFile.name}" after reaching the 500 MB uncompressed size limit.`,
      );
    }
  } catch (err) {
    const reason = err instanceof Error ? err.message : 'invalid archive';
    warnings.push(`Could not read zip archive "${zipFile.name}" (${reason}).`);
  }

  return { pdfs, skipped };
}

export async function extractPdfFilesFromList(
  input: FileList | File[],
): Promise<PdfIntakeResult> {
  const rawFiles = Array.from(input);
  const pdfs: File[] = [];
  let skippedCount = 0;
  const warnings: string[] = [];
  const budget = { remainingBytes: MAX_UNZIPPED_BYTES };

  for (const file of rawFiles) {
    const relPath =
      (file as File & { webkitRelativePath?: string }).webkitRelativePath ||
      file.name;
    if (isIgnoredEntryName(relPath)) {
      continue;
    }

    if (isPdfFilename(file.name)) {
      pdfs.push(file);
    } else if (isZipFilename(file.name)) {
      const unpacked = await unpackZipFile(file, budget, warnings);
      pdfs.push(...unpacked.pdfs);
      skippedCount += unpacked.skipped;
    } else {
      skippedCount += 1;
    }
  }

  if (pdfs.length > MAX_BULK_PDFS) {
    warnings.push(
      `Only the first ${MAX_BULK_PDFS} PDFs were kept (${pdfs.length - MAX_BULK_PDFS} extra skipped).`,
    );
    pdfs.length = MAX_BULK_PDFS;
  }

  return { files: pdfs, skippedCount, warnings };
}

export async function extractPdfFilesFromDataTransfer(
  dataTransfer: DataTransfer,
): Promise<PdfIntakeResult> {
  const items = Array.from(dataTransfer.items ?? []);
  const entries = items
    .map((item) => {
      const withEntry = item as DataTransferItem & {
        webkitGetAsEntry?: () => FileSystemEntryLike | null;
      };
      return typeof withEntry.webkitGetAsEntry === 'function'
        ? withEntry.webkitGetAsEntry()
        : null;
    })
    .filter((entry): entry is FileSystemEntryLike => entry !== null);

  if (entries.length === 0) {
    return extractPdfFilesFromList(dataTransfer.files);
  }

  const collected: File[] = [];
  for (const entry of entries) {
    await collectFromEntry(entry, collected);
  }
  return extractPdfFilesFromList(collected);
}
