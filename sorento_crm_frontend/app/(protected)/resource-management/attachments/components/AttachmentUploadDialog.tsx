'use client';

import { useState, useCallback, useEffect, useRef } from 'react';
import { AlertCircle } from 'lucide-react';
import { Button } from '@/components/ui/button';
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogFooter,
} from '@/components/ui/dialog';
import { SearchableSelect } from '@/components/common/SearchableSelect';
import { SearchableMultiSelect } from '@/components/common/SearchableMultiSelect';
import {
  DEFAULT_PACKING_LIST_REGIONS,
  PACKING_LIST_REGION_OPTIONS,
} from '@/lib/packing-list-regions';
import { FileDropzone } from '@/components/common/FileDropzone';
import { Label } from '@/components/ui/label';
import { Checkbox } from '@/components/ui/checkbox';
import { Alert, AlertDescription, AlertIcon } from '@/components/ui/alert';
import { useUploadAttachment, useAttachmentTypesList, useDirectoryTree } from '../hooks/useAttachments';
import { useUploadConflict } from '@/hooks/use-upload-conflict';
import { checkAttachmentCollision, type AttachmentConflictResolution } from '../services/attachmentService';
import type { AttachmentType } from '../../attachment-types/types/attachmentType.types';
import { flattenDirectoryOptions } from '../../attachment-directories/components/drivePath';
import { useContactAccessTypes } from '@/app/(protected)/user-management/contact-access-types/hooks/useContactAccessTypes';
import {
  groupFieldSpecs,
  useFieldLinkageSchema,
  type FieldLinkageEntityType,
} from '@/app/(protected)/master-data-management/products/hooks/useFieldLinkageSchema';
import { AccessLevelsMultiSelect } from './AccessLevelsMultiSelect';
import { useUploadManager } from '@/components/upload-activity';

interface AttachmentUploadDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onSuccess?: (attachmentId?: string) => void;
  entityType?: string;
  entityId?: string;
  /** When opening from browser with a folder selected, uploads go to this directory. */
  defaultDirectoryId?: string | null;
  /** Pre-select this attachment type on open (e.g. Packing List's Upload CTA, 7 Sep
   *  reversal) - same n8n-backed Create Attachment flow, just pointed at one type. */
  defaultTypeId?: string;
  /** Disable the type select once `defaultTypeId` is seeded - the caller has decided
   *  the type, the user only supplies files. */
  lockType?: boolean;
}

export default function AttachmentUploadDialog({
  open,
  onOpenChange,
  onSuccess,
  entityType: propEntityType,
  entityId: propEntityId,
  defaultDirectoryId,
  defaultTypeId,
  lockType,
}: AttachmentUploadDialogProps) {
  const [selectedTypeId, setSelectedTypeId] = useState<string>('');
  const [selectedFiles, setSelectedFiles] = useState<File[]>([]);
  const [entityType, setEntityType] = useState<string>(propEntityType || '');
  const [entityId, setEntityId] = useState<string>(propEntityId || '');
  const [accessLevels, setAccessLevels] = useState<string[]>([]);
  // Packing List type only; West preselected, never emptied.
  const [regions, setRegions] = useState<string[]>([...DEFAULT_PACKING_LIST_REGIONS]);
  // Only relevant when the caller left `defaultDirectoryId` unset OR passed root (a screen
  // with no folder context of its own, e.g. Packing Lists' Upload supplier documents, or the
  // Files browser at "All attachments") - a caller that DID pass a real folder id (opened from
  // inside a specific Drive folder) keeps deciding it, unchanged. Seeded from the picked type's
  // own default (R4) and always overridable.
  const [selectedDirectoryId, setSelectedDirectoryId] = useState<string>('');
  // Once the user picks a folder themselves, a later type switch must not clobber it with
  // that type's default (S3) - only auto-seeds before the user has touched the field.
  const [folderTouched, setFolderTouched] = useState(false);
  const [validationError, setValidationError] = useState<string>('');
  const [uploadProgress, setUploadProgress] = useState<Record<string, number>>({});
  const [isUploading, setIsUploading] = useState(false);
  // Field-linkage template (shown only when there's no entity_type forced
  // by the parent - e.g. opened from Resource Management → Files - AND the
  // selected attachment type is a product photo, since that's the only type
  // whose row link is meaningful here).
  const [targetEntityType, setTargetEntityType] = useState<FieldLinkageEntityType | ''>('');
  const [targetFieldKeys, setTargetFieldKeys] = useState<string[]>([]);

  const { data: attachmentTypes = [], isLoading: isLoadingTypes } = useAttachmentTypesList();
  // Root ("All attachments") reaches this dialog as either `undefined` (no prop passed
  // at all) or explicit `null` (AttachmentBrowser / AttachmentsInFolderPanel both pass
  // `directoryId ?? null`) - a real folder id is the only value that should skip the
  // picker and file straight into it (B3, purchasing consolidation batch 6 Sep 2026).
  const showFolderPicker = defaultDirectoryId == null;
  const { data: directoryTree = [] } = useDirectoryTree(false, { enabled: showFolderPicker });
  const directoryOptions = showFolderPicker ? flattenDirectoryOptions(directoryTree) : [];
  const {
    data: accessTypeOptions = [],
    error: accessTypesError,
    refetch: refetchAccessTypes,
  } = useContactAccessTypes();
  const defaultAccessLevels = accessTypeOptions.length > 0 ? accessTypeOptions.map((o) => o.code) : ['dealer', 'end_user'];
  // Phase 2: real uploader wired into the Upload Activity drawer. The
  // startSession `uploader` closure runs uploadMutation.mutateAsync for each
  // file; the drawer's optimistic session reconciles each entry into a real
  // attachment_id on resolve, or a post_failed state on reject.
  const uploadMutation = useUploadAttachment();
  // confirmConflict prompts Replace / Create copy while the modal is still
  // open (a pre-flight, before the background upload session starts - the
  // session itself can't surface a 409 interactively).
  const { ConflictDialog, confirmConflict } = useUploadConflict();
  const uploadManager = useUploadManager();

  const selectedType = attachmentTypes.find((type: AttachmentType) => type.id === selectedTypeId);
  // Never carry a previous opening's choice over: every opening starts on West only.
  useEffect(() => {
    if (open) setRegions([...DEFAULT_PACKING_LIST_REGIONS]);
  }, [open]);

  // Field linkage is now opt-in per attachment type (admin toggle), not a
  // hardcoded product-photo name check.
  const showRegions = selectedType?.code === 'packing_list';
  const showFieldLinkageSection = !propEntityType && !!selectedType?.supports_field_linkage;

  const { data: fieldLinkageSchema } = useFieldLinkageSchema(
    showFieldLinkageSection && targetEntityType ? targetEntityType : null,
  );

  // Reset form when dialog closes; re-seed `defaultTypeId` (Packing List's Upload CTA,
  // 7 Sep reversal) every time it opens instead - a caller that presets the type wants
  // that fresh on each open, not just the first mount.
  useEffect(() => {
    if (!open) {
      setSelectedTypeId('');
      setSelectedFiles([]);
      setEntityType(propEntityType || '');
      setEntityId(propEntityId || '');
      setAccessLevels(defaultAccessLevels);
      setValidationError('');
      setUploadProgress({});
      setIsUploading(false);
      setTargetEntityType('');
      setTargetFieldKeys([]);
      setSelectedDirectoryId('');
      setFolderTouched(false);
      return;
    }
    if (defaultTypeId) setSelectedTypeId(defaultTypeId);
  }, [open, propEntityType, propEntityId, defaultAccessLevels.join(','), defaultTypeId]);

  // Seed the folder from the preset type's own default (same rule the type-select
  // onChange handler applies) once the type list has loaded - `attachmentTypes` arrives
  // async, so this can't be folded into the open-effect above.
  useEffect(() => {
    if (!open || !defaultTypeId || !showFolderPicker || folderTouched) return;
    const picked = attachmentTypes.find((type: AttachmentType) => type.id === defaultTypeId);
    if (picked) setSelectedDirectoryId(picked.default_directory_id ?? '');
  }, [open, defaultTypeId, showFolderPicker, folderTouched, attachmentTypes]);

  // When the user changes target_entity_type, clear any stale field selection.
  useEffect(() => {
    setTargetFieldKeys([]);
  }, [targetEntityType]);

  // Defaults are seeded by the open-effect above. Re-filling whenever the
  // user clears the selection would defeat "Clear all" - leave it alone.

  // Update validation when type or files change
  useEffect(() => {
    if (selectedFiles.length > 0 && selectedType) {
      const invalidFiles = selectedFiles.filter(file => !validateFile(file, selectedType, false));
      if (invalidFiles.length > 0) {
        setValidationError(`${invalidFiles.length} file(s) failed validation`);
      } else {
        setValidationError('');
      }
    } else {
      setValidationError('');
    }
  }, [selectedFiles, selectedType]);

  const validateFile = (file: File, type: AttachmentType, showError: boolean = true): boolean => {
    // Reject macOS metadata files (._*)
    if (file.name.trim().startsWith('._')) {
      return false;
    }
    // Check file extension
    const allowedExtensions = type.allowed_extensions
      .split(',')
      .map((ext) => ext.trim().toLowerCase().replace('.', ''));
    const fileExt = file.name.split('.').pop()?.toLowerCase();

    if (!fileExt || !allowedExtensions.includes(fileExt)) {
      if (showError) {
        setValidationError(
          `File extension .${fileExt} is not allowed. Allowed extensions: ${type.allowed_extensions}`
        );
      }
      return false;
    }

    // Check file size
    const fileSizeMB = file.size / (1024 * 1024);
    if (fileSizeMB > type.max_file_size_mb) {
      if (showError) {
        setValidationError(
          `File size (${fileSizeMB.toFixed(2)} MB) exceeds maximum allowed size (${type.max_file_size_mb} MB)`
        );
      }
      return false;
    }

    return true;
  };

  // The dropzone raises onReject once per refused file. Batch them into the one
  // inline alert this dialog has always shown; the counter resets on the next
  // microtask so each drop reports its own tally.
  const rejectedCountRef = useRef(0);
  const noteRejected = useCallback(() => {
    rejectedCountRef.current += 1;
    setValidationError(`${rejectedCountRef.current} file(s) skipped (extension/size)`);
    queueMicrotask(() => {
      rejectedCountRef.current = 0;
    });
  }, []);

  const handleFilesChange = useCallback(
    (next: File[]) => {
      if (!selectedType) {
        setValidationError('Please select an attachment type first');
        return;
      }
      setSelectedFiles(next);
      setUploadProgress((prev) => {
        const kept: Record<string, number> = {};
        for (const file of next) {
          if (prev[file.name] !== undefined) kept[file.name] = prev[file.name];
        }
        return kept;
      });
    },
    [selectedType]
  );

  const getAcceptString = (type: AttachmentType | undefined): string | undefined => {
    if (!type) return undefined;
    return type.allowed_extensions
      .split(',')
      .map((ext) => `.${ext.trim()}`)
      .join(',');
  };

  const handleUpload = async () => {
    if (!selectedTypeId) {
      setValidationError('Please select an attachment type');
      return;
    }

    if (selectedFiles.length === 0) {
      setValidationError('Please select at least one file to upload');
      return;
    }

    if (validationError && !validationError.includes('skipped')) {
      return;
    }

    // One UUID per multi-file submit so the BE notification helpers can coalesce
    // the per-attachment n8n callbacks into a single email (see attachment_notification_helper).
    const uploadBatchId =
      typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function'
        ? crypto.randomUUID()
        : `${Date.now()}-${Math.random().toString(36).slice(2, 10)}`;

    // Snapshot dialog-level state into a closure so the optimistic
    // uploader survives modal close + state resets cleanly.
    const snapshot = {
      attachmentTypeId: selectedTypeId,
      entityType: entityType || propEntityType || undefined,
      entityId: entityId || propEntityId || undefined,
      accessLevels: [...accessLevels],
      regions: showRegions ? [...regions] : undefined,
      directoryId: showFolderPicker ? selectedDirectoryId || undefined : (defaultDirectoryId ?? undefined),
      targetEntityType:
        showFieldLinkageSection && targetEntityType ? targetEntityType : null,
      targetFieldKeys:
        showFieldLinkageSection && targetEntityType && targetFieldKeys.length > 0
          ? [...targetFieldKeys]
          : null,
    };

    // Pre-flight conflict resolution - done HERE, while the modal is still open,
    // because the background upload session can't surface a 409 interactively.
    // For each file colliding on (folder, name) we prompt Replace / Create copy /
    // Cancel; the chosen resolution is threaded into the session per file.
    const conflictChoice = new Map<string, AttachmentConflictResolution>();
    const filesToUpload: File[] = [];
    for (const file of selectedFiles) {
      let resolution: AttachmentConflictResolution | undefined;
      // Check both folder uploads AND no-folder ("All attachments") uploads - 
      // an undefined directory id checks the NULL-directory scope server-side.
      const check = await checkAttachmentCollision(file.name, snapshot.directoryId);
      if (check.collides) {
        const choice = await confirmConflict({
          existing_attachment_id: check.existing_attachment_id ?? '',
          existing_file_name: check.existing_file_name ?? file.name,
        });
        if (choice == null) continue; // cancelled → skip this file
        resolution = choice;
      }
      if (resolution) conflictChoice.set(file.name, resolution);
      filesToUpload.push(file);
    }
    if (filesToUpload.length === 0) {
      return; // every file cancelled - keep the modal open
    }

    uploadManager.startSession({
      files: [...filesToUpload],
      sessionType: filesToUpload.length === 1 ? 'single' : 'multi',
      uploadBatchId,
      uploader: async (file) => {
        const attachment = await uploadMutation.mutateAsync({
          file,
          attachmentTypeId: snapshot.attachmentTypeId,
          entityType: snapshot.entityType,
          entityId: snapshot.entityId,
          accessLevels: snapshot.accessLevels,
          regions: snapshot.regions,
          directoryId: snapshot.directoryId,
          targetEntityType: snapshot.targetEntityType,
          targetFieldKeys: snapshot.targetFieldKeys,
          onConflict: conflictChoice.get(file.name),
          uploadBatchId,
        });
        if (!attachment) {
          throw new Error('Upload returned no attachment');
        }
        return { attachment_id: attachment.id };
      },
    });
    onSuccess?.();
    onOpenChange(false);
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-2xl max-h-[90vh] overflow-y-auto">
        <DialogHeader>
          <DialogTitle>Create Attachment</DialogTitle>
        </DialogHeader>

        <div className="space-y-6 py-4">
          {/* Attachment Type Selection */}
          <div className="space-y-2">
            <Label htmlFor="attachment-type">
              Attachment Type <span className="text-destructive">*</span>
            </Label>
            <SearchableSelect
              id="attachment-type"
              value={selectedTypeId}
              onChange={(value) => {
                setSelectedTypeId(value);
                setSelectedFiles([]);
                setValidationError('');
                setRegions([...DEFAULT_PACKING_LIST_REGIONS]);
                // Linked-fields section only applies to product photos; reset it
                // whenever the type changes so a stale selection can't leak.
                setTargetEntityType('');
                setTargetFieldKeys([]);
                // Pre-select the type's own default folder (R4) - only when the caller
                // left the folder decision to this dialog (no real `defaultDirectoryId`)
                // AND the user has not already picked one themselves; a manual choice
                // survives a later type switch (S3) instead of being clobbered by that
                // type's default.
                if (showFolderPicker && !folderTouched) {
                  const picked = attachmentTypes.find((type: AttachmentType) => type.id === value);
                  setSelectedDirectoryId(picked?.default_directory_id ?? '');
                }
              }}
              options={attachmentTypes.map((type: AttachmentType) => ({
                value: type.id,
                label: type.type_name,
                description: type.description || undefined,
              }))}
              placeholder="Select attachment type"
              disabled={isLoadingTypes || lockType}
            />
            {selectedType && (
              <p className="text-xs text-muted-foreground mt-1">
                Allowed: {selectedType.allowed_extensions} • Max size: {selectedType.max_file_size_mb} MB
              </p>
            )}
          </div>

          {/* Folder - only when the caller did not already decide it (opened from
              inside a specific Drive folder passes `defaultDirectoryId` and skips this). */}
          {showFolderPicker && (
            <div className="space-y-2">
              <Label htmlFor="attachment-folder">Folder</Label>
              <SearchableSelect
                id="attachment-folder"
                value={selectedDirectoryId}
                onChange={(value) => {
                  setSelectedDirectoryId(value);
                  setFolderTouched(true);
                }}
                options={directoryOptions}
                placeholder="No folder (All files)"
                clearable
              />
            </div>
          )}

          {/* File Upload */}
          <div className="space-y-2">
            <Label>Files <span className="text-destructive">*</span></Label>
            <FileDropzone
              multiple
              disabled={isUploading}
              accept={getAcceptString(selectedType)}
              maxSizeMb={selectedType?.max_file_size_mb}
              files={selectedFiles}
              onFilesChange={handleFilesChange}
              onReject={noteRejected}
              title="Drag and drop files here, or click to browse"
              hint={
                selectedType
                  ? `Allowed: ${selectedType.allowed_extensions}`
                  : 'Select an attachment type first'
              }
              aria-label="Attachment files"
            />
          </div>

          {showFieldLinkageSection && (
            <div className="space-y-3 rounded-md border bg-muted/30 p-3">
              <div className="space-y-2">
                <Label htmlFor="link-to">Linked to</Label>
                <SearchableSelect
                  id="link-to"
                  value={targetEntityType || 'none'}
                  onChange={(value) => {
                    if (value === 'none') {
                      setTargetEntityType('');
                    } else {
                      setTargetEntityType(value as FieldLinkageEntityType);
                    }
                  }}
                  options={[
                    { value: 'none', label: 'Not linked' },
                    { value: 'product', label: 'Product' },
                    { value: 'promotion', label: 'Promotion' },
                    { value: 'form', label: 'Form' },
                    { value: 'packing_list', label: 'Packing List' },
                  ]}
                  placeholder="(Optional) pick the table this document describes"
                />
                <p className="text-xs text-muted-foreground">
                  Sets the table only. The actual row link is established later by n8n
                  (or manually from the attachment&apos;s Linkages tab).
                </p>
              </div>

              {targetEntityType && fieldLinkageSchema && fieldLinkageSchema.fields.length > 0 && (
                <div className="space-y-2">
                  <Label>Linked Fields</Label>
                  <p className="text-xs text-muted-foreground">
                    Tag the fields this document answers - they appear on the row&apos;s
                    detail page tooltip and inline in the AI agent response.
                  </p>
                  <div className="space-y-2 max-h-48 overflow-y-auto pr-1">
                    {groupFieldSpecs(fieldLinkageSchema.fields).map((group) => {
                      const isComposite = group.specs.length > 1;
                      const allSelected = group.specs.every((s) =>
                        targetFieldKeys.includes(s.name),
                      );
                      const someSelected = group.specs.some((s) =>
                        targetFieldKeys.includes(s.name),
                      );
                      return (
                        <div key={group.groupKey}>
                          {isComposite && (
                            <label className="flex items-center gap-2 text-sm font-medium">
                              <Checkbox
                                checked={
                                  allSelected
                                    ? true
                                    : someSelected
                                      ? 'indeterminate'
                                      : false
                                }
                                onCheckedChange={() => {
                                  setTargetFieldKeys((prev) => {
                                    const set = new Set(prev);
                                    if (allSelected) {
                                      for (const s of group.specs) set.delete(s.name);
                                    } else {
                                      for (const s of group.specs) set.add(s.name);
                                    }
                                    return Array.from(set);
                                  });
                                }}
                                data-testid={`upload-field-link-group-${group.groupKey}`}
                              />
                              {group.label}
                            </label>
                          )}
                          <div className={isComposite ? 'pl-6 mt-1 space-y-1' : 'space-y-1'}>
                            {group.specs.map((spec) => (
                              <label
                                key={spec.name}
                                className="flex items-center gap-2 text-sm"
                              >
                                <Checkbox
                                  checked={targetFieldKeys.includes(spec.name)}
                                  onCheckedChange={() => {
                                    setTargetFieldKeys((prev) => {
                                      const set = new Set(prev);
                                      if (set.has(spec.name)) set.delete(spec.name);
                                      else set.add(spec.name);
                                      return Array.from(set);
                                    });
                                  }}
                                  data-testid={`upload-field-link-key-${spec.name}`}
                                />
                                <span>{spec.label}</span>
                                {spec.unit && (
                                  <span className="text-xs text-muted-foreground">
                                    ({spec.unit})
                                  </span>
                                )}
                              </label>
                            ))}
                          </div>
                        </div>
                      );
                    })}
                  </div>
                </div>
              )}
            </div>
          )}

          {showRegions && (
            <div className="space-y-2">
              <Label htmlFor="attachment-regions">
                Regions <span className="text-destructive">*</span>
              </Label>
              <SearchableMultiSelect
                id="attachment-regions"
                value={regions}
                onChange={(next) => {
                  if (next.length > 0) setRegions(next);
                }}
                options={PACKING_LIST_REGION_OPTIONS}
              />
            </div>
          )}

          <div className="space-y-2" data-guide-target="resource-management.files.access-levels">
            <Label>Access Levels</Label>
            <AccessLevelsMultiSelect
              options={accessTypeOptions}
              loadError={accessTypesError}
              onRetry={() => void refetchAccessTypes()}
              value={accessLevels}
              onChange={setAccessLevels}
            />
          </div>

          {/* Validation Error */}
          {validationError && (
            <Alert variant="destructive" icon="destructive">
              <AlertIcon>
                <AlertCircle className="size-4" />
              </AlertIcon>
              <AlertDescription>{validationError}</AlertDescription>
            </Alert>
          )}
        </div>

        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button
            onClick={handleUpload}
            disabled={!selectedTypeId || selectedFiles.length === 0 || (!!validationError && !validationError.includes('skipped')) || isUploading}
            data-guide-target="resource-management.files.upload-confirm-button"
          >
            {isUploading
              ? `Uploading ${selectedFiles.filter(file => uploadProgress[file.name] === 100).length + 1} of ${selectedFiles.length}...`
              : `Upload ${selectedFiles.length} Attachment${selectedFiles.length !== 1 ? 's' : ''}`}
          </Button>
        </DialogFooter>
      </DialogContent>
      {ConflictDialog}
    </Dialog>
  );
}
