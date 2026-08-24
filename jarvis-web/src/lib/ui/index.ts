// The component library.
//
// Everything the console is built from, in one import. Each component owns its
// own tokens-only styling (`scripts/verify/token_lint.py --require-clean
// jarvis-web/src/lib/ui` refuses a raw value here), documents itself in a
// `<!-- @component` block, has a section in README.md, and is rendered in every
// state on `/styleguide`.
//
// The rule this replaces: primitives used to be CSS classes in `chrome.css`
// that every page hand-copied — `.jv-empty`'s markup appeared eight times, and
// eight pages meant eight chances to write a different empty state.
export { default as Button } from './Button.svelte';
export { default as IconButton } from './IconButton.svelte';
export { default as Input } from './Input.svelte';
export { default as Select } from './Select.svelte';
export { default as Toggle } from './Toggle.svelte';
export { default as Field } from './Field.svelte';
export { default as Panel } from './Panel.svelte';
export { default as Row } from './Row.svelte';
export { default as Pill } from './Pill.svelte';
export { default as Toolbar } from './Toolbar.svelte';
export { default as Tabs } from './Tabs.svelte';
export { default as Dialog } from './Dialog.svelte';
export { default as SkeletonRows } from './SkeletonRows.svelte';
export { default as EmptyState } from './EmptyState.svelte';
export { default as ErrorState } from './ErrorState.svelte';
export { default as OfflineState } from './OfflineState.svelte';
export { default as ScreenState } from './ScreenState.svelte';
export { default as Reactor } from './Reactor.svelte';

export type { Status } from './ScreenState.svelte';
