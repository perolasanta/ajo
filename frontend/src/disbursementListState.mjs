export function disbursementListState({ isPending, isError, data }) {
  if (isPending) return "loading";
  if (isError) return "error";
  if (!data) return "loading";
  return data.items.length === 0 ? "empty" : "results";
}
