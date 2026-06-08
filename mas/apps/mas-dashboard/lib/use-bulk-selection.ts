"use client";

import { useCallback, useMemo, useState } from "react";

/**
 * Tracks a set of selected row IDs and provides helpers for per-row toggle,
 * select-all, and clear. Designed for tables that need bulk actions.
 *
 * @param ids All IDs that could potentially be selected (e.g. currently visible
 *            rows after filtering). Used to compute the "all selected" and
 *            "indeterminate" states.
 */
export function useBulkSelection<T extends string = string>(ids: readonly T[]) {
  const [selected, setSelected] = useState<Set<T>>(new Set());

  const idSet = useMemo(() => new Set(ids), [ids]);

  const clear = useCallback(() => setSelected(new Set()), []);

  const toggle = useCallback((id: T) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }, []);

  // Drop any selected IDs that are no longer in `ids` (e.g. after a filter
  // change or a delete). Call this from an effect after `ids` changes.
  const prune = useCallback(() => {
    setSelected((prev) => {
      let mutated = false;
      const next = new Set<T>();
      prev.forEach((id) => {
        if (idSet.has(id)) next.add(id);
        else mutated = true;
      });
      return mutated ? next : prev;
    });
  }, [idSet]);

  const selectAll = useCallback(() => setSelected(new Set(ids)), [ids]);

  const isAllSelected = ids.length > 0 && selected.size === ids.length;
  // "Indeterminate" only counts IDs that are still in the current list —
  // ignoring stale selections from before a delete/filter change.
  const liveSelected = useMemo(() => {
    let n = 0;
    selected.forEach((id) => {
      if (idSet.has(id)) n++;
    });
    return n;
  }, [selected, idSet]);
  const isIndeterminate = liveSelected > 0 && liveSelected < ids.length;

  const toggleAll = useCallback(() => {
    if (isAllSelected) clear();
    else selectAll();
  }, [isAllSelected, clear, selectAll]);

  return {
    selected,
    selectedCount: liveSelected,
    clear,
    toggle,
    selectAll,
    toggleAll,
    isAllSelected,
    isIndeterminate,
    prune,
  };
}
