/** Plain-language labels for immutable policy and loan-product versions. */
export function versionStateLabel(item, today) {
  const effectiveFrom = item.effective_from ?? "";
  const replaces =
    item.replaces_version != null ? `Replaces version ${item.replaces_version}` : null;
  if (item.replaced_by_version != null) {
    const date = item.replaced_by_effective_from ?? item.effective_to;
    return [replaces, `Replaced by version ${item.replaced_by_version} on ${date}`]
      .filter(Boolean)
      .join(" · ");
  }
  if (effectiveFrom > today) {
    return [replaces, `Starts on ${effectiveFrom}`].filter(Boolean).join(" · ");
  }
  if (item.status === "approved") return [replaces, "In force now"].filter(Boolean).join(" · ");
  return item.status;
}
