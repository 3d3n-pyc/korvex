// The page's state and everything that changes it. No DOM here: the ui/
// modules read `state` and re-render when told something changed.
//
// Changes show at once (optimistic) and are then confirmed by the server;
// if it refuses, the old state comes back and the error is reported.

import { api } from "./api.js";
import { addDays, today } from "./dates.js";
import { debounce } from "./dom.js";

export const state = {
  lists: [],
  /** {kind: "today"} | {kind: "planned"} | {kind: "list", id} | {kind: "search", q} */
  view: { kind: "today" },
  tasks: [],
  counts: { today: 0, planned: 0 },
  selectedId: null,
  detailsOpen: false,
  live: { connected: false, pages: 0 },
  hidden: new Set(), // deleted, but can still be undone
};

const listeners = new Set();
let reportError = (error) => console.error(error);

export const onChange = (listener) => listeners.add(listener);
export const setErrorReporter = (reporter) => (reportError = reporter);
export function changed() {
  for (const listener of listeners) listener(state);
}

// --- Reading ----------------------------------------------------------------

export const listById = (id) => state.lists.find((list) => list.id === id);
export const taskById = (id) => state.tasks.find((task) => task.id === id);
export const visibleTasks = () => state.tasks.filter((task) => !state.hidden.has(task.id));
export const currentList = () => (state.view.kind === "list" ? listById(state.view.id) : null);

let loading = 0;

async function loadView() {
  const ticket = ++loading;
  const { view } = state;
  let tasks;
  if (view.kind === "today") tasks = await api.tasks({ done: false, due_to: today() });
  else if (view.kind === "planned") tasks = await api.tasks({ done: false, due_from: "1970-01-01" });
  else if (view.kind === "search") tasks = view.q ? await api.tasks({ q: view.q, limit: 100 }) : [];
  else tasks = await api.tasks({ list_id: view.id });
  if (ticket === loading) state.tasks = tasks; // a newer view was asked for meanwhile: drop this one
}

async function loadCounts() {
  const [due, planned] = await Promise.all([
    api.tasks({ done: false, due_to: today() }),
    api.tasks({ done: false, due_from: "1970-01-01" }),
  ]);
  state.counts = { today: due.length, planned: planned.length };
}

/** Reloads everything shown. */
export async function refresh() {
  [state.lists] = await Promise.all([api.lists(), loadCounts(), loadView()]);
  if (state.view.kind === "list" && !listById(state.view.id)) return go({ kind: "list", id: state.lists[0].id });
  if (state.selectedId && !taskById(state.selectedId)) closeDetails();
  changed();
}

/** Coalesces bursts (live events, several quick changes) into one reload. */
export const refreshSoon = debounce(() => refresh().catch(reportError), 60);

// --- Navigation: the view lives in the URL's hash, so Back works --------------

export function viewToHash(view) {
  if (view.kind === "list") return `#/list/${view.id}`;
  if (view.kind === "search") return `#/search/${encodeURIComponent(view.q)}`;
  return `#/${view.kind}`;
}

export function hashToView(hash) {
  const [, kind, rest] = hash.match(/^#\/(\w+)\/?(.*)$/) ?? [];
  if (kind === "today" || kind === "planned") return { kind };
  if (kind === "list" && rest) return { kind: "list", id: rest };
  if (kind === "search") return { kind: "search", q: decodeURIComponent(rest) };
  return null;
}

export async function go(view, { replace = false } = {}) {
  const hash = viewToHash(view);
  if (location.hash !== hash) history[replace ? "replaceState" : "pushState"](null, "", hash);
  const sameView = viewToHash(state.view) === hash;
  state.view = view;
  if (!sameView) {
    state.selectedId = null;
    state.detailsOpen = false;
    state.tasks = [];
  }
  changed();
  await loadView().catch(reportError);
  changed();
}

// --- Selection ------------------------------------------------------------------

export function select(id, { open = false } = {}) {
  state.selectedId = id;
  if (open) state.detailsOpen = true;
  changed();
}

export function closeDetails() {
  state.detailsOpen = false;
  changed();
}

// --- Tasks --------------------------------------------------------------------

function put(task) {
  const index = state.tasks.findIndex((each) => each.id === task.id);
  if (index === -1) state.tasks.unshift(task);
  else state.tasks[index] = task;
}

/** Where a task typed in the current view goes, and when it's due. */
export function defaultsForNewTask() {
  const { view } = state;
  return {
    listId: view.kind === "list" ? view.id : state.lists[0]?.id,
    due: view.kind === "today" || view.kind === "planned" ? today() : null,
  };
}

export async function addTask(title, { listId, due }) {
  try {
    const task = await api.createTask({ list_id: listId, title, due });
    if (state.view.kind !== "search") put(task);
    state.selectedId = task.id;
    changed();
  } catch (error) {
    reportError(error);
    throw error;
  }
  refreshSoon();
}

export async function updateTask(id, changes) {
  const before = taskById(id);
  if (!before) return;
  const optimistic = { ...before, ...changes };
  if ("done" in changes) optimistic.completed_at = changes.done ? new Date().toISOString() : null;
  put(optimistic);
  changed();
  try {
    put(await api.updateTask(id, changes));
  } catch (error) {
    put(before);
    reportError(error);
  }
  changed();
  refreshSoon(); // counts, sections, other lists
}

export const toggleTask = (id) => {
  const task = taskById(id);
  if (task) return updateTask(id, { done: !task.done });
};

/** Hides the task now and deletes it in 5 seconds; returns an undo. */
export function deleteTask(id) {
  if (!taskById(id) || state.hidden.has(id)) return () => {};
  const neighbours = visibleTasks().map((task) => task.id);
  const at = neighbours.indexOf(id);
  state.hidden.add(id);
  if (state.selectedId === id) {
    state.selectedId = neighbours[at + 1] ?? neighbours[at - 1] ?? null;
    state.detailsOpen = false;
  }
  const timer = setTimeout(() => commitDelete(id), 5000);
  pendingDeletes.set(id, timer);
  changed();
  return () => {
    clearTimeout(timer);
    pendingDeletes.delete(id);
    state.hidden.delete(id);
    changed();
  };
}

const pendingDeletes = new Map();

async function commitDelete(id, options) {
  pendingDeletes.delete(id);
  try {
    await api.deleteTask(id, options);
  } catch (error) {
    if (error.status !== 404) reportError(error);
  }
  state.hidden.delete(id);
  refreshSoon();
}

// Leaving the page with deletes still pending: send them now (keepalive
// lets the request finish after the page is gone).
addEventListener("pagehide", () => {
  for (const [id, timer] of pendingDeletes) {
    clearTimeout(timer);
    commitDelete(id, { keepalive: true });
  }
});

/** After a drag and drop: `ids` are the list's open tasks in their new order. */
export async function reorderTasks(listId, ids) {
  const rank = new Map(ids.map((id, index) => [id, index]));
  state.tasks.sort((a, b) => (rank.get(a.id) ?? Infinity) - (rank.get(b.id) ?? Infinity));
  changed();
  try {
    await api.reorder(listId, ids);
  } catch (error) {
    reportError(error);
    refreshSoon();
  }
}

export async function moveTask(id, listId) {
  await updateTask(id, { list_id: listId });
  if (state.view.kind === "list" && state.view.id !== listId) {
    state.tasks = state.tasks.filter((task) => task.id !== id);
    changed();
  }
}

/** Alt+↑ / Alt+↓: moves the selected open task one place in its list. */
export function nudgeSelected(step) {
  const list = currentList();
  const task = taskById(state.selectedId);
  if (!list || !task || task.done) return;
  const ids = visibleTasks().filter((each) => !each.done).map((each) => each.id);
  const from = ids.indexOf(task.id);
  const to = from + step;
  if (to < 0 || to >= ids.length) return;
  [ids[from], ids[to]] = [ids[to], ids[from]];
  reorderTasks(list.id, ids);
}

export const dueIn = (days) => addDays(today(), days);

// --- Lists --------------------------------------------------------------------

export async function createList(name) {
  const list = await api.createList({ name });
  state.lists.push(list);
  await go({ kind: "list", id: list.id });
  refreshSoon();
  return list;
}

export async function updateList(id, changes) {
  const before = listById(id);
  Object.assign(before, changes);
  changed();
  try {
    Object.assign(before, await api.updateList(id, changes));
  } catch (error) {
    reportError(error);
    refreshSoon();
  }
  changed();
}

export async function deleteList(id) {
  await api.deleteList(id);
  state.lists = state.lists.filter((list) => list.id !== id);
  await go({ kind: "list", id: state.lists[0].id }, { replace: true });
  refreshSoon();
}

export async function clearDone(listId) {
  const { deleted } = await api.clearDone(listId);
  state.tasks = state.tasks.filter((task) => !(task.done && task.list_id === listId));
  changed();
  refreshSoon();
  return deleted;
}
