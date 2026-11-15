export function openBankStatementPicker(
  input: { value: string; click(): void } | null,
): boolean;
export function parseBankStatementHeaders(csvText: string): string[];
export function suggestBankStatementMapping(headers: string[]): {
  date: string;
  narration: string;
  amount: string;
  credit: string;
  debit: string;
  reference: string;
};
