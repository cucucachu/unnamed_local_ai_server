import { ApiError } from '../api';
import { isStepUpRequired, StepUpCancelledError, withStepUp } from '../stepUp';

const stepUpRequired = () => new ApiError(403, 'step_up_required');

describe('withStepUp', () => {
  it('returns the result without prompting when the call succeeds', async () => {
    const confirm = jest.fn();
    await expect(withStepUp(async () => 'ok', confirm)).resolves.toBe('ok');
    expect(confirm).not.toHaveBeenCalled();
  });

  it('prompts on 403 step_up_required and retries once', async () => {
    const fn = jest.fn().mockRejectedValueOnce(stepUpRequired()).mockResolvedValueOnce('ok');
    const confirm = jest.fn().mockResolvedValue(true);

    await expect(withStepUp(fn, confirm)).resolves.toBe('ok');
    expect(confirm).toHaveBeenCalledTimes(1);
    expect(fn).toHaveBeenCalledTimes(2);
  });

  it('rejects with StepUpCancelledError (no retry) when the prompt is dismissed', async () => {
    const fn = jest.fn().mockRejectedValue(stepUpRequired());

    await expect(withStepUp(fn, async () => false)).rejects.toBeInstanceOf(StepUpCancelledError);
    expect(fn).toHaveBeenCalledTimes(1);
  });

  it('surfaces a second step_up_required instead of looping', async () => {
    const fn = jest.fn().mockRejectedValue(stepUpRequired());
    const confirm = jest.fn().mockResolvedValue(true);

    await expect(withStepUp(fn, confirm)).rejects.toMatchObject({ detail: 'step_up_required' });
    expect(confirm).toHaveBeenCalledTimes(1);
    expect(fn).toHaveBeenCalledTimes(2);
  });

  it('passes other errors straight through', async () => {
    const confirm = jest.fn();
    await expect(withStepUp(() => Promise.reject(new ApiError(403, 'admin_required')), confirm)).rejects.toMatchObject({
      detail: 'admin_required',
    });
    expect(confirm).not.toHaveBeenCalled();
  });
});

describe('isStepUpRequired', () => {
  it('matches only a 403 step_up_required', () => {
    expect(isStepUpRequired(stepUpRequired())).toBe(true);
    expect(isStepUpRequired(new ApiError(401, 'step_up_required'))).toBe(false);
    expect(isStepUpRequired(new ApiError(403, 'invalid_password'))).toBe(false);
    expect(isStepUpRequired(new Error('step_up_required'))).toBe(false);
  });
});
