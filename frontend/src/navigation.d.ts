import type { NotificationSummary } from "./api/schema";

export type NavigationItem = {
  name: string;
  path: string;
  roles: string[];
};

export const navigation: NavigationItem[];
export function visibleNavigation(roles: string[]): NavigationItem[];
export type AccountMenuBlock = {
  path: string;
  displayName: string;
  avatarName: string;
  email: string;
  roles: string[];
};
export function accountMenuBlocks(session: {
  full_name?: string | null;
  email: string;
  roles: string[];
}): AccountMenuBlock[];
export function menuActionCount(
  name: string,
  summary: NotificationSummary | undefined,
  roles: string[],
): number;
export function totalVisibleActionCount(
  visibleLinks: NavigationItem[],
  summary: NotificationSummary | undefined,
  roles: string[],
): number;
export function formatMenuBadge(count: number): string | null;
export function menuButtonLabel(count: number): string;
export const DEFAULT_LANDING_PATH: string;
export const ACCESS_DENIED_NOTICE: string;
export type RouteDecision =
  { allowed: true } | { allowed: false; redirectTo: string; notice?: string };
export function getRouteDecision(roles: string[], pathname: string): RouteDecision;
