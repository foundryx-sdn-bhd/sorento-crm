# UAC: CONTACT-COPY-FIELDS

C1. Copying from a source contact to one or many targets makes each target's stock check mode and locations
    (the contact's Stock visibility override) equal to the source's. A source with no override leaves the
    target with no override.
C2. Companies are replaced with the source's set (extra companies on the target are removed).
C3. Market segments are replaced with the source's set.
C4. The preview lists every change for the four fields with human labels (mode names, location codes,
    company names, segment names), and the apply writes exactly what the preview showed.
C5. A user who cannot set companies directly (not superadmin/admin) does not change companies via copy;
    the dialog names companies under "Not copied". Same for stock check mode + locations without
    `inventory.stock.edit`.
C6. The dialog's source summary shows the four fields.
