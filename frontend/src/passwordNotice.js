export const PASSWORD_CHANGED_NOTICE = "Password changed. Your other sessions were signed out.";
export const PASSWORD_NOTICE_DURATION_MS = 10_000;

export function passwordNoticeAfterEdit(shownAt, now) {
  if (shownAt === null || now - shownAt >= PASSWORD_NOTICE_DURATION_MS) return null;
  return shownAt;
}
