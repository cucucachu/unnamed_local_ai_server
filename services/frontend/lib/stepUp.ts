import { ApiError } from './api';

/** Thrown by `withStepUp` when the user dismisses the password prompt.
 * Callers treat it as "nothing happened", not as a failure to show. */
export class StepUpCancelledError extends Error {
  constructor() {
    super('step-up cancelled');
    this.name = 'StepUpCancelledError';
  }
}

export function isStepUpRequired(error: unknown): boolean {
  return error instanceof ApiError && error.status === 403 && error.detail === 'step_up_required';
}

/** Runs `fn`; on `403 step_up_required` asks `confirmPassword` to step the
 * session up (resolving `false` if the user cancels) and retries `fn`
 * once. A second `step_up_required` is surfaced like any other error. */
export async function withStepUp<T>(fn: () => Promise<T>, confirmPassword: () => Promise<boolean>): Promise<T> {
  try {
    return await fn();
  } catch (error) {
    if (!isStepUpRequired(error)) throw error;
  }
  if (!(await confirmPassword())) throw new StepUpCancelledError();
  return fn();
}
