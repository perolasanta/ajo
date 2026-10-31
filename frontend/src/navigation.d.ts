export type NavigationItem = {
  name: string;
  path: string;
  roles: string[];
};

export const navigation: NavigationItem[];
export function visibleNavigation(roles: string[]): NavigationItem[];
