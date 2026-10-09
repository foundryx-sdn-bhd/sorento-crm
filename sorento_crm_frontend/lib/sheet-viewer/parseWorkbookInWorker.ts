import type { SpreadsheetWorkbook } from './types';

const PARSE_TIMEOUT_MS = 20_000;

/** Parses off the main thread. The main-thread parser is used only where `Worker` does not exist. */
export async function parseWorkbookInWorker(
  bytes: ArrayBuffer,
  filename: string,
): Promise<SpreadsheetWorkbook> {
  if (typeof Worker === 'undefined') {
    const { parseWorkbook } = await import('./parseWorkbook');
    return parseWorkbook(bytes, filename);
  }
  return new Promise<SpreadsheetWorkbook>((resolve, reject) => {
    const worker = new Worker(new URL('./parseWorkbook.worker.ts', import.meta.url), {
      type: 'module',
    });
    const finish = (settle: () => void) => {
      clearTimeout(timer);
      worker.terminate();
      settle();
    };
    const timer = setTimeout(
      () => finish(() => reject(new Error('Parsing timed out'))),
      PARSE_TIMEOUT_MS,
    );
    worker.onmessage = (event: MessageEvent<{ ok: boolean; workbook?: SpreadsheetWorkbook; message?: string }>) => {
      const data = event.data;
      finish(() =>
        data.ok && data.workbook ? resolve(data.workbook) : reject(new Error(data.message ?? 'Parse failed')),
      );
    };
    worker.onerror = () => finish(() => reject(new Error('Parse failed')));
    worker.postMessage({ bytes, filename }, [bytes]);
  });
}
