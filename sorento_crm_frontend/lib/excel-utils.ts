/**
 * Excel utility functions for generating and parsing Excel files
 * Requires: npm install xlsx
 */

// Dynamic import to avoid SSR issues
let XLSX: any = null;

async function getXLSX() {
  if (typeof window === 'undefined') {
    throw new Error('Excel utilities can only be used in the browser');
  }
  if (!XLSX) {
    XLSX = await import('xlsx');
  }
  return XLSX;
}

export interface ColumnOption {
  key: string;
  label: string;
  selected: boolean;
}

/**
 * Generate Excel file from data with selected columns
 */
export async function generateExcelFile(
  data: any[],
  columns: ColumnOption[],
  filename: string = 'export.xlsx'
): Promise<void> {
  const xlsx = await getXLSX();
  
  // Filter columns to only selected ones
  const selectedColumns = columns.filter(col => col.selected);
  
  if (selectedColumns.length === 0) {
    throw new Error('Please select at least one column to export');
  }
  
  // Prepare worksheet data
  const worksheetData = data.map(row => {
    const rowData: Record<string, any> = {};
    selectedColumns.forEach(col => {
      // Handle nested keys (e.g., 'customer.customer_name')
      const keys = col.key.split('.');
      let value = row;
      for (const key of keys) {
        value = value?.[key];
        if (value === undefined || value === null) break;
      }
      rowData[col.label] = value ?? '';
    });
    return rowData;
  });
  
  // Create workbook and worksheet
  const worksheet = xlsx.utils.json_to_sheet(worksheetData);
  const workbook = xlsx.utils.book_new();
  xlsx.utils.book_append_sheet(workbook, worksheet, 'Sheet1');
  
  // Generate Excel file and download
  xlsx.writeFile(workbook, filename);
}

/** Sheet name macro workbooks must expose their import data on (mirrors backend rule). */
const XLSM_DATA_SHEET = 'Template';

/**
 * Pick the sheet to import. `.xlsx`/`.xls` keep today's behavior (first sheet).
 * `.xlsm` mirrors the backend macro pipeline: the "Template" sheet
 * (case-insensitive) if present; a single-sheet workbook keeps its only sheet;
 * multi-sheet without Template throws (backend would 422 the same upload).
 */
export function resolveImportSheetName(filename: string, sheetNames: string[]): string {
  if (!filename.toLowerCase().endsWith('.xlsm')) {
    return sheetNames[0];
  }
  const template = sheetNames.find((n) => n.toLowerCase() === XLSM_DATA_SHEET.toLowerCase());
  if (template) return template;
  if (sheetNames.length === 1) return sheetNames[0];
  throw new Error(
    `Macro workbook must contain a '${XLSM_DATA_SHEET}' sheet (found sheets: ${sheetNames.join(', ')}).`,
  );
}

/**
 * Pick a sheet by its configured name, case-insensitive. Unlike `resolveImportSheetName` this
 * never falls back to a `Template` sheet or to the only sheet: a workbook without the named
 * sheet is an error that names the sheets it does have.
 */
export function resolveNamedSheet(sheetNames: string[], sheetName: string): string {
  const wanted = sheetName.trim().toLowerCase();
  const found = sheetNames.find((n) => n.trim().toLowerCase() === wanted);
  if (found) return found;
  throw new Error(`Sheet '${sheetName}' not found (found sheets: ${sheetNames.join(', ')}).`);
}

/**
 * Parse Excel file and return data as array of objects. With `{ sheetName }` the named sheet
 * is read (delivery order compare); without it the products / stock rule of
 * `resolveImportSheetName` applies.
 */
export async function parseExcelFile(file: File, options?: { sheetName?: string }): Promise<any[]> {
  const xlsx = await getXLSX();

  return new Promise((resolve, reject) => {
    const reader = new FileReader();

    reader.onload = (e) => {
      try {
        const data = new Uint8Array(e.target?.result as ArrayBuffer);
        const workbook = xlsx.read(data, { type: 'array' });
        const sheetName = options?.sheetName
          ? resolveNamedSheet(workbook.SheetNames, options.sheetName)
          : resolveImportSheetName(file.name, workbook.SheetNames);
        const worksheet = workbook.Sheets[sheetName];
        const jsonData = xlsx.utils.sheet_to_json(worksheet);
        resolve(jsonData);
      } catch (error) {
        reject(new Error('Failed to parse Excel file: ' + (error instanceof Error ? error.message : 'Unknown error')));
      }
    };
    
    reader.onerror = () => {
      reject(new Error('Failed to read file'));
    };
    
    reader.readAsArrayBuffer(file);
  });
}

/**
 * Parse Excel file and return all sheets by name
 */
export async function parseExcelSheets(file: File): Promise<{
  sheetNames: string[];
  sheets: Record<string, any[]>;
}> {
  const xlsx = await getXLSX();

  return new Promise((resolve, reject) => {
    const reader = new FileReader();

    reader.onload = (e) => {
      try {
        const data = new Uint8Array(e.target?.result as ArrayBuffer);
        const workbook = xlsx.read(data, { type: 'array' });
        const sheets: Record<string, any[]> = {};

        workbook.SheetNames.forEach((sheetName: string) => {
          const worksheet = workbook.Sheets[sheetName];
          sheets[sheetName] = xlsx.utils.sheet_to_json(worksheet);
        });

        resolve({ sheetNames: workbook.SheetNames, sheets });
      } catch (error) {
        reject(new Error('Failed to parse Excel file: ' + (error instanceof Error ? error.message : 'Unknown error')));
      }
    };

    reader.onerror = () => {
      reject(new Error('Failed to read file'));
    };

    reader.readAsArrayBuffer(file);
  });
}

/**
 * Get column headers from Excel file
 */
export async function getExcelHeaders(file: File): Promise<string[]> {
  const xlsx = await getXLSX();
  
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    
    reader.onload = (e) => {
      try {
        const data = new Uint8Array(e.target?.result as ArrayBuffer);
        const workbook = xlsx.read(data, { type: 'array' });
        const firstSheetName = workbook.SheetNames[0];
        const worksheet = workbook.Sheets[firstSheetName];
        const headers = xlsx.utils.sheet_to_json(worksheet, { header: 1 })[0] as string[];
        resolve(headers || []);
      } catch (error) {
        reject(new Error('Failed to parse Excel file headers: ' + (error instanceof Error ? error.message : 'Unknown error')));
      }
    };
    
    reader.onerror = () => {
      reject(new Error('Failed to read file'));
    };
    
    reader.readAsArrayBuffer(file);
  });
}
