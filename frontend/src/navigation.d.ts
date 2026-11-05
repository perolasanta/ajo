export type NavigationItem = {
  name: string;
  path: string;
  roles: string[];
};

export const navigation: NavigationItem[];
export function visibleNavigation(roles: string[]): NavigationItem[];
export const DEFAULT_LANDING_PATH: string;
export const ACCESS_DENIED_NOTICE: string;
export type RouteDecision =
  | { allowed: true }
  | { allowed: false; redirectTo: string; notice?: string };
export function getRouteDecision(roles: string[], pathname: string): RouteDecision;
