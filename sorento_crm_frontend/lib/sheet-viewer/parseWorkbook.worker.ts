/// <reference lib="webworker" />
import { parseWorkbook } from './parseWorkbook';

export interface ParseRequest {
  bytes: ArrayBuffer;
  filename: string;
}

self.onmessage = async (event: MessageEvent<ParseRequest>) => {
  try {
    const workbook = await parseWorkbook(event.data.bytes, event.data.filename);
    self.postMessage({ ok: true, workbook });
  } catch (error) {
    self.postMessage({
      ok: false,
      message: error instanceof Error ? error.message : 'Parse failed',
    });
  }
};
