export function openBankStatementPicker(input) {
  if (!input) return false;
  input.value = "";
  input.click();
  return true;
}
