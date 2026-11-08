export type AttemptKeyState = Map<string, string>;
export function createAttemptKeyState(): AttemptKeyState;
export function getAttemptKey(
  state: AttemptKeyState,
  formName: string,
  createKey?: () => string,
): string;
export function completeAttempt(state: AttemptKeyState, formName: string): void;
