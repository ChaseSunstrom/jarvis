// Is this browser on a network at all?
//
// A page distinguishes two failures that look identical on screen and are not:
// the relay socket closed (jarvis-core restarted, the token expired) and the
// machine has no network (the laptop's lid was shut, the wifi dropped). The
// first is worth a RECONNECT button; the second is worth saying so, because
// pressing reconnect on a laptop with no network re-dials into the same wall.
//
// `navigator.onLine` is famously weak — it means "the OS thinks there is a
// route", not "the internet works" — which is exactly the distinction wanted
// here: it is the OS's opinion about the machine, not about Jarvis.

/** True when the browser believes it has a network. Assumes yes off-browser. */
export function isOnline(): boolean {
	if (typeof navigator === 'undefined' || typeof navigator.onLine !== 'boolean') return true;
	return navigator.onLine;
}

/**
 * Call `handler` whenever the browser's idea of being online changes, and once
 * immediately with the current value. Returns the unsubscribe.
 */
export function watchOnline(handler: (online: boolean) => void): () => void {
	if (typeof window === 'undefined') return () => {};
	const update = () => handler(isOnline());
	window.addEventListener('online', update);
	window.addEventListener('offline', update);
	update();
	return () => {
		window.removeEventListener('online', update);
		window.removeEventListener('offline', update);
	};
}
