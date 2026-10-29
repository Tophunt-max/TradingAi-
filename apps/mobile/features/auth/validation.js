// Mirrors backend password policy (backend remains the source of truth).
export function passwordProblems(pw) {
  if (pw.length < 10) return 'Use at least 10 characters.';
  if (pw.length > 128) return 'Use at most 128 characters.';
  if (!/[A-Za-z]/.test(pw) || !/\d/.test(pw)) return 'Include at least one letter and one number.';
  return null;
}

export function isSixDigitCode(code) {
  return /^\d{6}$/.test(code);
}
