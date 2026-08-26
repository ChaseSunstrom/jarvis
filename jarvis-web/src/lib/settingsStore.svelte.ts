// The editable settings, as one page's working copy.
//
// Three settings sections (Assistant, Voice, House) each show a few plain rows
// and an EVERYTHING fold of the raw ones, and every row needs the same five
// things: the server's rows, a draft per key so typing does not fight the
// server's copy, which row is saving, which row was refused and why, and
// which saved rows still need a restart. That was one component's worth of
// state on the old single settings page; split across three sections it would
// be three copies, and three copies of "drop a draft once the server confirms
// it" is three chances to keep showing a field as edited after it was saved.
//
// So it is a class with runes, owned by a section and handed to its rows.
// Nothing here knows about a connection: the section passes the client in when
// it has one, and the store is inert until then.

import type { JarvisClient, SettingRow, SettingResult } from './jarvisClient';
import { coerceSetting } from './settingsDraft';
import { describeError } from './connection';
import { toasts } from './toast';

export class SettingsStore {
	rows = $state<SettingRow[]>([]);
	/** False once the backend has answered "no such command": an older jarvis-core. */
	supported = $state(true);
	/** True once the first list has arrived (or failed), so a skeleton knows when to go. */
	loaded = $state(false);
	/** Per-key draft values. Absent means "what the server said". */
	drafts = $state<Record<string, string>>({});
	busyKey = $state('');
	fieldError = $state<Record<string, string>>({});
	/** Keys saved this session that only take effect after a restart. */
	restartNeeded = $state<string[]>([]);

	private client: JarvisClient | null = null;

	/** The rows of one server group, in the server's order. */
	group(name: string): SettingRow[] {
		return this.rows.filter((row) => row.group === name);
	}

	/** Every group the server sent, in first-seen order. */
	get groups(): string[] {
		const seen: string[] = [];
		for (const row of this.rows) if (!seen.includes(row.group)) seen.push(row.group);
		return seen;
	}

	row(key: string): SettingRow | undefined {
		return this.rows.find((row) => row.key === key);
	}

	draftOf(row: SettingRow): string {
		return this.drafts[row.key] ?? (row.value == null ? '' : String(row.value));
	}

	setDraft(key: string, value: string): void {
		this.drafts = { ...this.drafts, [key]: value };
	}

	isDirty(row: SettingRow): boolean {
		const current = row.value == null ? '' : String(row.value);
		return this.drafts[row.key] !== undefined && this.drafts[row.key] !== current;
	}

	/** The choices a `<Select>` offers, with the configured value kept even when it is not among them. */
	choicesOf(row: SettingRow): { value: string; label: string }[] {
		const choices = (row.choices ?? []).map((choice) => ({ value: choice, label: choice }));
		const current = this.draftOf(row);
		// What is configured is not among what could be discovered. Shown rather
		// than silently reset to the first option.
		if (!(row.choices ?? []).includes(current)) {
			choices.push({ value: current, label: current || '(unset)' });
		}
		return choices;
	}

	/** Take the server's rows, dropping every draft it has now confirmed. */
	adopt(rows: SettingRow[]): void {
		this.rows = rows;
		const next: Record<string, string> = {};
		for (const row of rows) {
			const draft = this.drafts[row.key];
			const current = row.value == null ? '' : String(row.value);
			if (draft !== undefined && draft !== current) next[row.key] = draft;
		}
		this.drafts = next;
	}

	/** Load from `client`, and remember it for the saves that follow. */
	async load(client: JarvisClient | null): Promise<void> {
		this.client = client;
		if (!client) return;
		try {
			this.adopt((await client.listSettings())?.settings ?? []);
		} catch (e) {
			// An older jarvis-core has no settings API. The rest of the page is
			// still useful, so say so once rather than showing an error.
			this.supported = false;
			console.warn('settings unavailable', e);
		} finally {
			this.loaded = true;
		}
	}

	/** Fold a `set`/`reset` answer in: the rows, and whether a restart is owed. */
	absorb(result: SettingResult): void {
		this.adopt(result.settings ?? this.rows);
		const rest = this.restartNeeded.filter((k) => k !== result.key);
		this.restartNeeded = result.restart_required ? [...rest, result.key] : rest;
	}

	async save(row: SettingRow): Promise<void> {
		if (!this.client) return;
		this.busyKey = row.key;
		this.fieldError = { ...this.fieldError, [row.key]: '' };
		try {
			const result = await this.client.setSetting(row.key, coerceSetting(row.type, this.draftOf(row)));
			this.absorb(result);
			toasts.success(`${row.label} saved`, result.restart_required ? 'restart to apply' : 'in effect now');
		} catch (e) {
			this.fieldError = { ...this.fieldError, [row.key]: describeError(e) };
		} finally {
			this.busyKey = '';
		}
	}

	async reset(row: SettingRow): Promise<void> {
		if (!this.client) return;
		this.busyKey = row.key;
		this.fieldError = { ...this.fieldError, [row.key]: '' };
		try {
			const result = await this.client.resetSetting(row.key);
			const { [row.key]: _dropped, ...rest } = this.drafts;
			this.drafts = rest;
			this.absorb(result);
			toasts.success(`${row.label} reset`);
		} catch (e) {
			this.fieldError = { ...this.fieldError, [row.key]: describeError(e) };
		} finally {
			this.busyKey = '';
		}
	}
}
