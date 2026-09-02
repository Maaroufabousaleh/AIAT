"use client";

import { clsx } from "clsx";
import { CheckSquare, Square, X, Trash2, Archive } from "lucide-react";

export type BulkAction = "delete" | "archive";

export interface BulkActionBarProps {
  selectedCount: number;
  totalCount: number;
  loading?: boolean;
  /** Action to perform on the selected items. */
  action: BulkAction;
  /** Label override for the action button. Defaults to "Delete N" / "Archive N". */
  actionLabel?: string;
  /** Confirmation message shown before performing the action. */
  confirmMessage?: string;
  onAction: () => void;
  onClear: () => void;
  /** Optional classes for the outer container. */
  className?: string;
}

/**
 * Slim action bar that appears above a table when the user has selected one or
 * more rows. Shows the selection count, a "clear" button, and a destructive
 * action button (delete or archive).
 */
export function BulkActionBar({
  selectedCount,
  totalCount,
  loading,
  action,
  actionLabel,
  confirmMessage,
  onAction,
  onClear,
  className,
}: BulkActionBarProps) {
  if (selectedCount === 0) return null;

  const isDelete = action === "delete";
  const defaultLabel = isDelete
    ? `Delete ${selectedCount}`
    : `Archive ${selectedCount}`;
  const label = actionLabel ?? defaultLabel;
  const Icon = isDelete ? Trash2 : Archive;
  const colorClasses = isDelete
    ? "bg-red-600 hover:bg-red-500 text-white"
    : "bg-amber-600 hover:bg-amber-500 text-white";
  const defaultConfirm = isDelete
    ? `Delete ${selectedCount} item${selectedCount === 1 ? "" : "s"}? This cannot be undone.`
    : `Archive ${selectedCount} item${selectedCount === 1 ? "" : "s"}?`;

  return (
    <div
      className={clsx(
        "flex items-center gap-3 px-4 py-2.5 rounded-lg border",
        isDelete
          ? "bg-red-950/40 border-red-800/60"
          : "bg-amber-950/40 border-amber-800/60",
        className
      )}
      role="region"
      aria-label="Bulk actions"
    >
      <CheckSquare size={16} className={isDelete ? "text-red-400" : "text-amber-400"} />
      <span className={clsx("text-sm font-medium", isDelete ? "text-red-200" : "text-amber-200")}>
        {selectedCount} selected
        {totalCount > selectedCount && (
          <span className="text-xs opacity-70 ml-1.5">of {totalCount}</span>
        )}
      </span>

      <div className="ml-auto flex items-center gap-2">
        <button
          type="button"
          onClick={onClear}
          disabled={loading}
          className="inline-flex min-h-11 items-center gap-1 px-2.5 py-1 text-xs text-gray-300 hover:text-white rounded transition-colors disabled:opacity-50"
        >
          <X size={12} />
          Clear
        </button>
        <button
          type="button"
          onClick={() => {
            if (confirmMessage ?? defaultConfirm) {
              if (!window.confirm(confirmMessage ?? defaultConfirm)) return;
            }
            onAction();
          }}
          disabled={loading}
          data-testid={`bulk-${action}-button`}
          className={clsx(
            "inline-flex min-h-11 items-center gap-1.5 px-3 py-1 text-xs font-medium rounded transition-colors disabled:opacity-50",
            colorClasses
          )}
        >
          <Icon size={12} />
          {loading ? "Working..." : label}
        </button>
      </div>
    </div>
  );
}

export interface SelectAllCheckboxProps {
  checked: boolean;
  indeterminate?: boolean;
  onChange: (checked: boolean) => void;
  /** Optional aria-label override. */
  ariaLabel?: string;
  /** Optional className for the wrapper button. */
  className?: string;
}

/**
 * Tri-state checkbox used as the "select all" header in bulk-select tables.
 * Renders checked, unchecked, or indeterminate (some-but-not-all) state.
 */
export function SelectAllCheckbox({
  checked,
  indeterminate,
  onChange,
  ariaLabel = "Select all rows",
  className,
}: SelectAllCheckboxProps) {
  const isOn = checked || indeterminate;
  return (
    <button
      type="button"
      role="checkbox"
      aria-checked={indeterminate ? "mixed" : checked}
      aria-label={ariaLabel}
      onClick={(e) => {
        e.stopPropagation();
        onChange(!checked);
      }}
      className={clsx(
        "inline-flex min-h-11 min-w-11 items-center justify-center text-gray-400 hover:text-white transition-colors",
        className
      )}
    >
      {isOn ? (
        <CheckSquare size={14} className="text-blue-400" />
      ) : (
        <Square size={14} />
      )}
    </button>
  );
}

export interface RowCheckboxProps {
  checked: boolean;
  onChange: (checked: boolean) => void;
  ariaLabel?: string;
  disabled?: boolean;
  /** When true, prevents the parent row's onClick from firing. */
  stopPropagation?: boolean;
  className?: string;
}

/**
 * Per-row checkbox used inside bulk-select tables. Renders a click target that
 * does not steal events from the surrounding row.
 */
export function RowCheckbox({
  checked,
  onChange,
  ariaLabel,
  disabled,
  stopPropagation,
  className,
}: RowCheckboxProps) {
  return (
    <button
      type="button"
      role="checkbox"
      aria-checked={checked}
      aria-label={ariaLabel}
      disabled={disabled}
      onClick={(e) => {
        if (stopPropagation) e.stopPropagation();
        e.preventDefault();
        if (disabled) return;
        onChange(!checked);
      }}
      className={clsx(
        "inline-flex min-h-11 min-w-11 items-center justify-center text-gray-400 hover:text-white transition-colors disabled:opacity-50 disabled:cursor-not-allowed",
        className
      )}
    >
      {checked ? (
        <CheckSquare size={14} className="text-blue-400" />
      ) : (
        <Square size={14} />
      )}
    </button>
  );
}
