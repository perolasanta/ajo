export function versionStateLabel(
  item: {
    status: string;
    effective_from?: string | null;
    effective_to?: string | null;
    replaces_version?: number | null;
    replaced_by_version?: number | null;
    replaced_by_effective_from?: string | null;
  },
  today: string,
): string;
