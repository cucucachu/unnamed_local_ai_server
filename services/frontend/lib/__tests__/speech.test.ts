import { isSpeechSupported, startListening, stopListening } from '../speech';

describe('speech (native stub)', () => {
  it('is never supported — Expo Go has no in-app speech module', () => {
    expect(isSpeechSupported()).toBe(false);
  });

  it('startListening is a no-op and returns a no-op stop', () => {
    const onInterim = jest.fn();
    const onFinal = jest.fn();
    const onError = jest.fn();
    const stop = startListening({ onInterim, onFinal, onError });
    expect(typeof stop).toBe('function');
    expect(() => stop()).not.toThrow();
    expect(() => stopListening()).not.toThrow();
    expect(onInterim).not.toHaveBeenCalled();
    expect(onFinal).not.toHaveBeenCalled();
    expect(onError).not.toHaveBeenCalled();
  });
});
