# UAC: the OI Excel is one sheet (OI-EXCEL-ONE-SHEET)

- AC-1 The Order Inquiries list export downloads a workbook with exactly one sheet, `ORDER
  INQUIRY`, holding every filtered row whatever its delivery month, undated rows included.
- AC-2 With the delivery month filter set, the export is still that one sheet, with only that
  month's rows.
- AC-3 The reorder run's OI worksheet (10 columns) and the handover attachment sent on Confirm and
  on resend are the same one sheet.
- AC-4 An empty result is the one sheet with its title and heading rows.
- AC-5 Rows keep the supplier, item code, delivery date order; TOTAL QTY prints on the last row of
  a supplier + item code run.
