export function createAttemptKeyState() {
  return new Map();
}

export function getAttemptKey(state, formName, createKey = () => crypto.randomUUID()) {
  if (!state.has(formName)) state.set(formName, createKey());
  return state.get(formName);
}

export function completeAttempt(state, formName) {
  state.delete(formName);
}
