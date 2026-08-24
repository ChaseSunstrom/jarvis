<!--
	The style guide: every design token, rendered, from the generated table.

	Not in the console's nav on purpose — the tab strip is mirrored on the phone
	(`ConsoleTab.kt`) and pinned by `console_parity_test.py`, and a reference page
	is not a destination the phone needs. Reach it at /styleguide. Everything on
	this page is drawn with tokens only; it is the first page the token lint has
	no baseline for, so a raw value here fails `make verify-all`.

	What this page shows and what it does not: the token groups, the type ramp,
	the spacing rhythm, radii, elevation, motion (animated at the token's own
	duration), the chrome primitives the console already has, and the four screen
	states every screen must implement. The component library and the reactor
	component arrive with milestone M02 and will be added here as they land.
-->
<script lang="ts">
	import { TOKENS, tokenMs, type TokenName } from '$lib/tokens';
	import {
		Button,
		IconButton,
		Input,
		Select,
		Toggle,
		Field,
		Panel,
		Row,
		Pill,
		Toolbar,
		Tabs,
		Dialog,
		SkeletonRows,
		EmptyState,
		ErrorState,
		OfflineState,
		ScreenState,
		Reactor
	} from '$lib/ui';

	type Row = { name: TokenName; value: string };
	const all = Object.entries(TOKENS) as [TokenName, string][];
	const rows = (test: (n: string) => boolean): Row[] =>
		all.filter(([n]) => test(n)).map(([name, value]) => ({ name, value }));

	const colours = rows((n) => !/^--jv-(font|fs|weight|track|space|radius|glow|elev|dur|ease|stagger|drift|rx|grid|bracket|focus)-/.test(n) && n !== '--jv-drift');
	const chromeColours = colours.filter((r) => !r.name.startsWith('--jv-orb-'));
	const orbColours = colours.filter((r) => r.name.startsWith('--jv-orb-'));
	const families = rows((n) => n.startsWith('--jv-font-'));
	const sizes = rows((n) => n.startsWith('--jv-fs-'));
	const tracking = rows((n) => n.startsWith('--jv-track-'));
	const weights = rows((n) => n.startsWith('--jv-weight-'));
	const spaces = rows((n) => n.startsWith('--jv-space-'));
	const radii = rows((n) => n.startsWith('--jv-radius-'));
	const elevation = rows((n) => n.startsWith('--jv-glow-') || n.startsWith('--jv-elev-'));
	const durations = rows((n) => n.startsWith('--jv-dur-') || n.startsWith('--jv-stagger-'));
	const eases = rows((n) => n.startsWith('--jv-ease-'));
	const reactor = rows((n) => n.startsWith('--jv-rx-'));

	// WCAG contrast of a hex text colour on the ground, so the page says which
	// text colours may carry words (>= 4.5) and which are marks only.
	function lum(hex: string): number {
		const h = hex.replace('#', '');
		const n = parseInt(h.length === 3 ? h.replace(/(.)/g, '$1$1') : h.slice(0, 6), 16);
		const ch = (v: number) => {
			const s = v / 255;
			return s <= 0.04045 ? s / 12.92 : Math.pow((s + 0.055) / 1.055, 2.4);
		};
		return 0.2126 * ch((n >> 16) & 255) + 0.7152 * ch((n >> 8) & 255) + 0.0722 * ch(n & 255);
	}
	function contrast(fg: string): string {
		if (!fg.startsWith('#')) return '—';
		const a = lum(fg), b = lum(TOKENS['--jv-bg']);
		const [hi, lo] = a > b ? [a, b] : [b, a];
		return ((hi + 0.05) / (lo + 0.05)).toFixed(1) + ':1';
	}
	const short = (n: string) => n.replace('--jv-', '');

	// The chrome demos are real controls: each one says what it did, because a
	// button that does nothing is exactly what the dead-control check exists to catch.
	let pressed = $state('');
	const press = (what: string) => () => (pressed = `${what} pressed`);

	// Live demo state, so every component on this page is the real one doing the
	// real thing rather than a screenshot of one.
	let demoText = $state('light.kitchen_lamp');
	let demoChoice = $state('deep');
	let demoOn = $state(true);
	let demoTab = $state('running');
	let demoDialog = $state(false);
	let demoStatus = $state<'loading' | 'ready' | 'empty' | 'error' | 'offline'>('ready');
	const STATUSES = ['loading', 'ready', 'empty', 'error', 'offline'] as const;
</script>

<svelte:head><title>Style guide · Jarvis</title></svelte:head>

<div class="guide">
	<header class="head">
		<p class="eyebrow">Design system · Reactor II</p>
		<h1>Every token, rendered</h1>
		<p class="lede">
			Generated from <code>design/tokens.json</code> by <code>design/build.py</code>. Nothing on this
			page — or anywhere in the console — types a colour, size or duration of its own; the token lint
			in <code>make verify-all</code> refuses it. {all.length} tokens.
		</p>
	</header>

	<section data-tokens="color" aria-labelledby="h-color">
		<h2 id="h-color">Colour</h2>
		<p class="sg-note">Text colours list their contrast on the ground. Below 4.5:1 is a mark, not a word — <code>tick</code> is the one such token, and it says so.</p>
		<ul class="swatches">
			{#each chromeColours as c (c.name)}
				<li class="swatch">
					<span class="chip" style={`background:var(${c.name})`}></span>
					<span class="sw-name">{short(c.name)}</span>
					<span class="sw-value">{c.value}</span>
					<span class="sw-meta">{contrast(c.value)}</span>
				</li>
			{/each}
		</ul>
		<h3>The reactor's palette</h3>
		<p class="sg-note">Pinned across the shader, the phone and the console by <code>reactor_orb_test.py</code>; drift-checked here by <code>build.py --check</code>.</p>
		<ul class="swatches compact">
			{#each orbColours as c (c.name)}
				<li class="swatch"><span class="chip" style={`background:var(${c.name})`}></span><span class="sw-name">{short(c.name)}</span><span class="sw-value">{c.value}</span></li>
			{/each}
		</ul>
	</section>

	<section data-tokens="type" aria-labelledby="h-type">
		<h2 id="h-type">Type</h2>
		<div class="families">
			{#each families as f (f.name)}
				<div class="family" style={`font-family:var(${f.name})`}>
					<span class="sw-name">{short(f.name)}</span>
					<p class="specimen">The kitchen lamp is at 40 % — I'll remind you at 18:00.</p>
					<span class="sw-value">{f.value}</span>
				</div>
			{/each}
		</div>
		<ol class="ramp">
			{#each sizes as s (s.name)}
				<li><span class="sw-name">{short(s.name)}</span><span class="sample" style={`font-size:var(${s.name})`}>Two minutes fourteen</span><span class="sw-value">{s.value}</span></li>
			{/each}
		</ol>
		<ul class="pairs">
			{#each tracking as t (t.name)}<li><span class="sw-name">{short(t.name)}</span><span class="tracked" style={`letter-spacing:var(${t.name})`}>LISTENING</span><span class="sw-value">{t.value}</span></li>{/each}
			{#each weights as w (w.name)}<li><span class="sw-name">{short(w.name)}</span><span style={`font-weight:var(${w.name})`}>Weight {w.value}</span><span class="sw-value">{w.value}</span></li>{/each}
		</ul>
	</section>

	<section data-tokens="space" aria-labelledby="h-space">
		<h2 id="h-space">Space</h2>
		<ul class="bars">
			{#each spaces as s (s.name)}
				<li><span class="sw-name">{short(s.name)}</span><span class="bar" style={`width:var(${s.name})`}></span><span class="sw-value">{s.value}</span></li>
			{/each}
		</ul>
	</section>

	<section data-tokens="radius" aria-labelledby="h-radius">
		<h2 id="h-radius">Radius</h2>
		<ul class="boxes">
			{#each radii as r (r.name)}
				<li><span class="box" style={`border-radius:var(${r.name})`}></span><span class="sw-name">{short(r.name)}</span><span class="sw-value">{r.value}</span></li>
			{/each}
		</ul>
	</section>

	<section data-tokens="elevation" aria-labelledby="h-elevation">
		<h2 id="h-elevation">Elevation</h2>
		<p class="sg-note">Glow is light, and it is budgeted: the reactor core, the current step, the push-to-talk ring. Everything else is flat.</p>
		<ul class="boxes">
			{#each elevation as e (e.name)}
				<li><span class="box" style={`box-shadow:var(${e.name})`}></span><span class="sw-name">{short(e.name)}</span><span class="sw-value">{e.value}</span></li>
			{/each}
		</ul>
	</section>

	<section data-tokens="motion" aria-labelledby="h-motion">
		<h2 id="h-motion">Motion</h2>
		<p class="sg-note">Each bar sweeps at its token's own duration. Under <code>prefers-reduced-motion</code> nothing here moves.</p>
		<ul class="bars motion">
			{#each durations as d (d.name)}
				<li><span class="sw-name">{short(d.name)}</span><span class="sweep" style={`animation-duration:var(${d.name})`}></span><span class="sw-value">{d.value} · {tokenMs(d.name)} ms</span></li>
			{/each}
			{#each eases as e (e.name)}
				<li><span class="sw-name">{short(e.name)}</span><span class="sweep eased" style={`animation-timing-function:var(${e.name})`}></span><span class="sw-value">{e.value}</span></li>
			{/each}
		</ul>
		<h3>The reactor's clock</h3>
		<ul class="pairs">
			{#each reactor as r (r.name)}<li><span class="sw-name">{short(r.name)}</span><span class="sw-value">{r.value}</span></li>{/each}
		</ul>
	</section>

	<section data-tokens="chrome" aria-labelledby="h-chrome" class="chrome-demo">
		<h2 id="h-chrome">Chrome</h2>
		<p class="sg-note">
			The grid, the brackets and the focus ring the console frame draws, on the same tokens.
		</p>
		<div class="sg-toolbar">
			<button class="btn" type="button" onclick={press('Ghost')}>Ghost</button>
			<button class="btn primary" type="button" onclick={press('Primary')}>Primary</button>
			<span class="pill">pill</span>
			<span class="pill on">on</span>
			<span class="sw-value" aria-live="polite">{pressed}</span>
		</div>
	</section>

	<section data-components aria-labelledby="h-components">
		<h2 id="h-components">Components</h2>
		<p class="sg-note">
			Every export of <code>$lib/ui</code>, live. Each one is documented in
			<code>src/lib/ui/README.md</code> and refuses a typed value — the token lint keeps this
			directory clean.
		</p>

		<div class="gallery">
			<article class="demo">
				<h3>Button · IconButton</h3>
				<div class="stack-h">
					<Button onclick={press('Ghost')}>Ghost</Button>
					<Button variant="primary" onclick={press('Approve')}>Approve</Button>
					<Button variant="danger" onclick={press('Deny')}>Deny</Button>
					<Button disabled title="Pick a row first">Disabled</Button>
					<IconButton label="Dismiss" glyph="×" onclick={press('Dismiss')} />
				</div>
				<p class="sw-value" aria-live="polite">{pressed}</p>
			</article>

			<article class="demo">
				<h3>Field · Input · Select · Toggle</h3>
				<div class="stack-v">
					<Field label="Entity" hint="The id Jarvis will act on">
						<Input bind:value={demoText} mono />
					</Field>
					<Field label="Depth" error={demoChoice === 'deep' ? '' : 'Quick reads fewer pages'}>
						<Select
							bind:value={demoChoice}
							options={[
								{ value: 'quick', label: 'Quick lookup' },
								{ value: 'deep', label: 'Deep research' }
							]}
						/>
					</Field>
					<Toggle bind:checked={demoOn} label="Exposed to Jarvis" hint="Voice can control it" />
				</div>
			</article>

			<article class="demo">
				<h3>Panel · Row · Pill</h3>
				<Panel title="This turn" meta="1.27 s" live>
					{#snippet children()}
						<Row label="transcribe" value="412 ms" />
						<Row label="first token" value="640 ms" current />
						<Row label="speak" value="210 ms" />
						<Row label="tools">
							{#snippet children()}
								<span class="stack-h">
									<Pill>tier 1</Pill>
									<Pill tone="live">running</Pill>
									<Pill tone="ok">ok</Pill>
									<Pill tone="warn">held</Pill>
									<Pill tone="danger">failed</Pill>
								</span>
							{/snippet}
						</Row>
					{/snippet}
				</Panel>
			</article>

			<article class="demo">
				<h3>Toolbar · Tabs · Dialog</h3>
				<Toolbar>
					{#snippet children()}
						<Input bind:value={demoText} placeholder="Filter" />
					{/snippet}
					{#snippet end()}
						<Button variant="primary" onclick={() => (demoDialog = true)}>Open dialog</Button>
					{/snippet}
				</Toolbar>
				<Tabs
					bind:selected={demoTab}
					tabs={[
						{ id: 'all', label: 'All' },
						{ id: 'running', label: 'Running', count: 2, live: true },
						{ id: 'done', label: 'Finished', count: 6 }
					]}
				/>
				<Dialog
					open={demoDialog}
					title="Forget this repository?"
					onclose={() => (demoDialog = false)}
				>
					{#snippet children()}
						<p>The files stay on disk. Jarvis stops tracking it and forgets its jobs.</p>
					{/snippet}
					{#snippet actions()}
						<Button onclick={() => (demoDialog = false)}>Cancel</Button>
						<Button variant="danger" onclick={() => (demoDialog = false)}>Forget</Button>
					{/snippet}
				</Dialog>
			</article>

			<article class="demo wide">
				<h3>Reactor</h3>
				<p class="sw-value">
					One instrument at every size: the voice orb, a task's progress ring, a dashboard figure.
				</p>
				<div class="stack-h reactors">
					<Reactor size={160} level={0.38} state="listening" label="Listening" />
					<Reactor
						size={160}
						level={0.61}
						segments={{ done: 2, running: 1, total: 5 }}
						breathing={false}
						label="Step 3 of 5"
					/>
					<Reactor size={110} level={0.62} state="thinking" label="Thinking" />
				</div>
			</article>
		</div>
	</section>

	<section data-states aria-labelledby="h-states">
		<h2 id="h-states">The four states every screen implements</h2>
		<p class="sg-note">
			<code>ScreenState</code> owns loading, empty, error and offline, so a screen cannot forget
			one. Drive it here; <code>e2e/states.spec.ts</code> drives it on every real screen.
		</p>
		<div class="sg-toolbar">
			{#each STATUSES as s (s)}
				<Button onclick={() => (demoStatus = s)} testid="state-{s}">{s}</Button>
			{/each}
		</div>
		<div class="state-stage">
			<ScreenState
				status={demoStatus}
				rows={3}
				emptyTitle="Nothing running"
				emptyBody="Research runs and scheduled jobs appear here. Ask Jarvis for something."
				errorTitle="Couldn't load tasks"
				errorDetail="The backend answered 500. Retry, or check docker compose logs jarvis-core."
				onretry={press('Retry')}
				onreconnect={press('Reconnect')}
			>
				{#snippet children()}
					<Panel title="Tasks" meta="2 running" live>
						{#snippet children()}
							<Row label="Add an OFFLINE state to the settings screen" value="3 / 5" current />
							<Row label="Research: whisper-large-v3-turbo on CPU" value="1 / 6" />
						{/snippet}
					</Panel>
				{/snippet}
			</ScreenState>
		</div>
		<h3>The pieces, on their own</h3>
		<div class="gallery">
			<article class="demo"><SkeletonRows rows={2} /></article>
			<article class="demo">
				<EmptyState title="Nothing here yet" body="One sentence on how something arrives." />
			</article>
			<article class="demo">
				<ErrorState
					title="Couldn't load tasks"
					detail="The backend answered 500."
					onretry={press('Retry')}
				/>
			</article>
			<article class="demo">
				<OfflineState onreconnect={press('Reconnect')} />
			</article>
		</div>
	</section>
</div>

<style>
	.guide { max-width: 72rem; margin: 0 auto; padding: var(--jv-space-6) var(--jv-space-5) var(--jv-space-7); display: grid; gap: var(--jv-space-7); color: var(--jv-text); }
	.head { display: grid; gap: var(--jv-space-2); }
	.eyebrow { font: var(--jv-weight-label) var(--jv-fs-2xs) / 1 var(--jv-font-body); letter-spacing: var(--jv-track-wide); text-transform: uppercase; color: var(--jv-text-dim); }
	h1 { font: var(--jv-weight-display) var(--jv-fs-display) / 1.1 var(--jv-font-display); color: var(--jv-text-bright); margin: 0; }
	h2 { font: var(--jv-weight-label) var(--jv-fs-xs) / 1 var(--jv-font-body); letter-spacing: var(--jv-track-wide); text-transform: uppercase; color: var(--jv-text-dim); margin: 0 0 var(--jv-space-4); padding-bottom: var(--jv-space-3); border-bottom: 1px solid var(--jv-line-hair); }
	h3 { font: var(--jv-weight-label) var(--jv-fs-2xs) / 1 var(--jv-font-body); letter-spacing: var(--jv-track-chrome); text-transform: uppercase; color: var(--jv-text-faint); margin: var(--jv-space-5) 0 var(--jv-space-3); }
	.lede, .sg-note { color: var(--jv-text-dim); max-width: 70ch; margin: 0 0 var(--jv-space-4); }
	code { font-family: var(--jv-font-chrome); font-size: var(--jv-fs-xs); color: var(--jv-text); }
	ul, ol { list-style: none; margin: 0; padding: 0; }
	.sw-name { font-family: var(--jv-font-chrome); font-size: var(--jv-fs-2xs); color: var(--jv-text); }
	.sw-value { font-family: var(--jv-font-chrome); font-size: var(--jv-fs-2xs); color: var(--jv-text-faint); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
	.sw-meta { font-family: var(--jv-font-chrome); font-size: var(--jv-fs-2xs); color: var(--jv-text-dim); text-align: right; }
	.swatches { display: grid; grid-template-columns: repeat(auto-fill, minmax(14rem, 1fr)); gap: var(--jv-space-2); }
	.swatch { display: grid; grid-template-columns: auto 1fr auto; grid-template-areas: 'chip name meta' 'chip value value'; column-gap: var(--jv-space-3); align-items: center; padding: var(--jv-space-2); border: 1px solid var(--jv-line-hair); border-radius: var(--jv-radius-md); background: var(--jv-panel); }
	.swatch .chip { grid-area: chip; width: var(--jv-space-6); height: var(--jv-space-6); border-radius: var(--jv-radius-sm); border: 1px solid var(--jv-line-soft); }
	.swatch .sw-name { grid-area: name; } .swatch .sw-value { grid-area: value; } .swatch .sw-meta { grid-area: meta; }
	.compact .swatch { grid-template-columns: auto 1fr; grid-template-areas: 'chip name' 'chip value'; }
	.families { display: grid; grid-template-columns: repeat(auto-fit, minmax(18rem, 1fr)); gap: var(--jv-space-4); margin-bottom: var(--jv-space-5); }
	.family { display: grid; gap: var(--jv-space-2); padding: var(--jv-space-4); border: 1px solid var(--jv-line-hair); border-radius: var(--jv-radius-md); background: var(--jv-panel); }
	.specimen { font-size: var(--jv-fs-lg); color: var(--jv-text-bright); margin: 0; }
	.ramp li, .pairs li, .bars li, .boxes li { display: grid; grid-template-columns: 8rem 1fr auto; align-items: center; gap: var(--jv-space-4); padding: var(--jv-space-2) 0; border-bottom: 1px solid var(--jv-line-hair); }
	.ramp .sample { color: var(--jv-text-bright); font-family: var(--jv-font-display); font-weight: var(--jv-weight-display); line-height: 1.2; }
	.tracked { font-family: var(--jv-font-body); font-weight: var(--jv-weight-label); text-transform: uppercase; font-size: var(--jv-fs-xs); }
	.bars .bar { display: block; height: var(--jv-space-2); background: var(--jv-accent-deep); border-radius: var(--jv-radius-sm); }
	.boxes .box { display: block; width: var(--jv-space-7); height: var(--jv-space-6); background: var(--jv-panel); border: 1px solid var(--jv-line); }
	.boxes li { grid-template-columns: auto 8rem 1fr; }
	.motion .sweep { display: block; height: var(--jv-space-2); background: linear-gradient(90deg, var(--jv-accent-deep), var(--jv-accent)); transform-origin: left; animation: sweep infinite alternate var(--jv-ease-in-out); border-radius: var(--jv-radius-sm); }
	.motion .sweep.eased { animation-duration: var(--jv-dur-enter); }
	@keyframes sweep { from { transform: scaleX(0.08); } to { transform: scaleX(1); } }
	@media (prefers-reduced-motion: reduce) { .motion .sweep { animation: none; transform: none; } }
	.sg-toolbar { display: flex; align-items: center; gap: var(--jv-space-3); margin-bottom: var(--jv-space-4); }
	.sg-panel { border: 1px solid var(--jv-line-hair); border-radius: var(--jv-radius-md); background: var(--jv-panel); }
	.sg-panel-head { display: flex; justify-content: space-between; align-items: baseline; padding: var(--jv-space-3) var(--jv-space-4); border-bottom: 1px solid var(--jv-line-hair); }
	.sg-panel-head h3 { margin: 0; }
	.sg-row { display: flex; justify-content: space-between; padding: var(--jv-space-3) var(--jv-space-4); border-bottom: 1px solid var(--jv-line-hair); }
	.sg-field { display: grid; gap: var(--jv-space-1); padding: var(--jv-space-3) var(--jv-space-4); }
	.sg-field span { font-family: var(--jv-font-chrome); font-size: var(--jv-fs-2xs); letter-spacing: var(--jv-track-chrome); text-transform: uppercase; color: var(--jv-text-faint); }
	.sg-field input { font: inherit; color: var(--jv-text-bright); background: var(--jv-field); border: 1px solid var(--jv-line-soft); border-radius: var(--jv-radius-sm); padding: var(--jv-space-2) var(--jv-space-3); }
	.gallery { display: grid; grid-template-columns: repeat(auto-fit, minmax(20rem, 1fr)); gap: var(--jv-space-4); }
	.demo { display: grid; gap: var(--jv-space-3); align-content: start; padding: var(--jv-space-4); border: 1px solid var(--jv-line-hair); border-radius: var(--jv-radius-md); background: var(--jv-bg-raised); }
	.demo.wide { grid-column: 1 / -1; }
	.demo h3 { margin: 0; }
	.stack-h { display: flex; flex-wrap: wrap; align-items: center; gap: var(--jv-space-3); }
	.stack-v { display: grid; gap: var(--jv-space-4); }
	.reactors { justify-content: space-around; }
	.state-stage { min-height: var(--jv-space-7); margin-bottom: var(--jv-space-4); }
</style>
