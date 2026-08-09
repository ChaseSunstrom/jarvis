import { describe, it, expect, vi, afterEach } from 'vitest';
import {
	MAX_TOASTS,
	TOAST_TTL_MS,
	ToastBus,
	humanizeService,
	serviceFailureText,
	serviceSuccessText
} from './toast';

afterEach(() => vi.useRealTimers());

describe('ToastBus', () => {
	it('notifies a new subscriber immediately with the current list', () => {
		const bus = new ToastBus();
		bus.info('already here');
		const seen: unknown[] = [];
		bus.subscribe((list) => seen.push(list));
		expect(seen).toHaveLength(1);
		expect((seen[0] as any[])[0].text).toBe('already here');
	});

	it('pushes in arrival order and hands out unique ids', () => {
		const bus = new ToastBus();
		const a = bus.success('one');
		const b = bus.error('two');
		expect(a).not.toBe(b);
		expect(bus.list.map((t) => t.text)).toEqual(['one', 'two']);
		expect(bus.list.map((t) => t.kind)).toEqual(['success', 'error']);
	});

	it('drops the oldest past the cap rather than growing forever', () => {
		const bus = new ToastBus(3);
		for (const text of ['a', 'b', 'c', 'd']) bus.info(text);
		expect(bus.list.map((t) => t.text)).toEqual(['b', 'c', 'd']);
	});

	it('hands out a copy, so a caller cannot mutate the bus', () => {
		const bus = new ToastBus();
		bus.info('x');
		bus.list.length = 0;
		expect(bus.list).toHaveLength(1);
	});

	it('auto-dismisses after the kind-specific ttl', () => {
		vi.useFakeTimers();
		const bus = new ToastBus();
		bus.success('gone soon');
		vi.advanceTimersByTime(TOAST_TTL_MS.success - 1);
		expect(bus.list).toHaveLength(1);
		vi.advanceTimersByTime(2);
		expect(bus.list).toHaveLength(0);
	});

	// A failure the user has to act on must not evaporate faster than a success.
	it('keeps errors up longer than successes', () => {
		expect(TOAST_TTL_MS.error).toBeGreaterThan(TOAST_TTL_MS.success);
	});

	it('ttl 0 pins a toast until it is dismissed', () => {
		vi.useFakeTimers();
		const bus = new ToastBus();
		const id = bus.push('info', 'sticky', undefined, 0);
		vi.advanceTimersByTime(60_000);
		expect(bus.list).toHaveLength(1);
		bus.dismiss(id);
		expect(bus.list).toHaveLength(0);
	});

	it('dismissing twice, or an unknown id, does not re-notify', () => {
		const bus = new ToastBus();
		const id = bus.info('x');
		const seen: number[] = [];
		bus.subscribe((list) => seen.push(list.length));
		bus.dismiss(id);
		bus.dismiss(id);
		bus.dismiss(999);
		expect(seen).toEqual([1, 0]);
	});

	it('clear() empties everything and cancels the pending timers', () => {
		vi.useFakeTimers();
		const bus = new ToastBus();
		bus.info('a');
		bus.error('b');
		bus.clear();
		expect(bus.list).toEqual([]);
		expect(() => vi.advanceTimersByTime(60_000)).not.toThrow();
		expect(bus.list).toEqual([]);
	});

	it('unsubscribing stops the callbacks', () => {
		const bus = new ToastBus();
		const seen: number[] = [];
		const off = bus.subscribe((list) => seen.push(list.length));
		bus.info('a');
		off();
		bus.info('b');
		expect(seen).toEqual([0, 1]);
	});

	it('defaults its cap to MAX_TOASTS', () => {
		const bus = new ToastBus();
		for (let i = 0; i < MAX_TOASTS + 2; i += 1) bus.info(String(i));
		expect(bus.list).toHaveLength(MAX_TOASTS);
	});
});

describe('service call copy', () => {
	it('turns a service name into something readable', () => {
		expect(humanizeService('turn_on')).toBe('Turn on');
		expect(humanizeService('media_previous_track')).toBe('Media previous track');
		expect(humanizeService('')).toBe('Call');
	});

	it('names the entity in both the success and the failure line', () => {
		expect(serviceSuccessText('turn_on', 'Lab Lights')).toBe('Turn on · Lab Lights');
		expect(serviceFailureText('unlock', 'Front Door')).toBe('Unlock failed · Front Door');
	});
});
