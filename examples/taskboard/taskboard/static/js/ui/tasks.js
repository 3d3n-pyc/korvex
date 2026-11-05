// The middle of the page: the view's title, the "Add a task" field, and
// the tasks, in sections.

import { dueLabel, longDate, plannedGroup, today } from "../dates.js";
import { $, append, h, highlight, icon } from "../dom.js";
import { sortable } from "../dragdrop.js";
import {
  addTask,
  clearDone,
  currentList,
  defaultsForNewTask,
  deleteList,
  dueIn,
  listById,
  reorderTasks,
  select,
  state,
  toggleTask,
  updateList,
  visibleTasks,
} from "../store.js";
import { closeMenu, confirm, menu, toast } from "./overlays.js";

const COLORS = ["blue", "green", "orange", "red", "purple", "pink", "teal", "gray"];
const DUE_CHOICES = [
  { label: "No date", due: () => null },
  { label: "Today", due: () => dueIn(0) },
  { label: "Tomorrow", due: () => dueIn(1) },
  { label: "Next week", due: () => dueIn(7) },
];
let dueChoice = null; // index in DUE_CHOICES, or null for the view's default
let showDone = false;
let lastView = "";

export function setup() {
  const form = $("#add-task");
  const input = $("#add-title");
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const title = input.value.trim();
    if (!title) return;
    const defaults = defaultsForNewTask();
    const due = dueChoice === null ? defaults.due : DUE_CHOICES[dueChoice].due();
    input.value = "";
    try {
      await addTask(title, { listId: defaults.listId, due });
    } catch {
      input.value = title; // give it back, to fix and retry
    }
  });
  $("#add-due").addEventListener("click", () => {
    const current = dueChoice ?? DUE_CHOICES.findIndex((choice) => choice.due() === defaultsForNewTask().due);
    dueChoice = (current + 1) % DUE_CHOICES.length;
    renderDueChip();
    input.focus();
  });
  input.addEventListener("keydown", (event) => {
    if (event.key === "Escape") input.blur();
  });
  $("#list-menu-button").addEventListener("click", (event) => listMenu(event.currentTarget));
}

export function focusNewTask() {
  $("#add-title").focus();
}

// --- Rendering ------------------------------------------------------------------

export function render() {
  const { view } = state;
  const list = currentList();
  const titles = {
    today: ["Today", longDate()],
    planned: ["Planned", "Everything with a due date"],
    search: ["Search", view.q ? `Results for “${view.q}”` : ""],
    list: [list?.name ?? "", list ? `${list.open_tasks} open` : ""],
  };
  const [title, subtitle] = titles[view.kind];
  const viewKey = JSON.stringify(view);
  if (viewKey !== lastView) {
    lastView = viewKey;
    dueChoice = null; // each view has its own default due date
    showDone = false;
  }
  $("#view-title").textContent = title;
  $("#view-subtitle").textContent = subtitle;
  document.title = title ? `${title} · Taskboard` : "Taskboard";
  document.documentElement.style.setProperty("--accent", `var(--c-${list?.color ?? (view.kind === "today" ? "orange" : "blue")})`);
  $("#list-menu-button").hidden = view.kind !== "list";
  $("#add-task").hidden = view.kind === "search";
  renderDueChip();

  const area = $("#task-area");
  const scroll = area.scrollTop;
  area.replaceChildren();
  append(area, sections()); // skips the false and null of conditional parts
  area.scrollTop = scroll;
}

function renderDueChip() {
  const due = dueChoice === null ? defaultsForNewTask().due : DUE_CHOICES[dueChoice].due();
  const chip = $("#add-due");
  chip.classList.toggle("set", Boolean(due));
  chip.querySelector("span").textContent = due ? dueLabel(due).text : "";
  chip.title = due ? `Due ${dueLabel(due).text.toLowerCase()} (click to change)` : "Add a due date";
}

function sections() {
  const tasks = visibleTasks();
  const { view } = state;

  if (view.kind === "list") {
    const open = tasks.filter((task) => !task.done);
    const done = tasks.filter((task) => task.done);
    if (!tasks.length) return [empty("sparkle", "All clear", "Nothing in this list. Add a task above.")];
    const openList = h("ul", { class: "tasks", id: "open-tasks" }, open.map((task) => row(task, { sortable: true })));
    sortable(openList, (ids) => reorderTasks(view.id, ids));
    return [
      openList,
      open.length === 0 && h("p", { class: "all-done" }, icon("sparkle"), "Everything's done here."),
      done.length > 0 && completedSection(done),
    ];
  }

  if (view.kind === "search") {
    if (!view.q) return [empty("search", "Search your tasks", "Type in the search box: titles and notes are searched.")];
    if (!tasks.length) return [empty("search", "Nothing found", `No task mentions “${view.q}”.`)];
    return [group(`${tasks.length} task${tasks.length > 1 ? "s" : ""}`, tasks, { showList: true, query: view.q })];
  }

  if (!tasks.length) {
    return view.kind === "today"
      ? [empty("sun", "Nothing due today", "Enjoy your day, or add something above.")]
      : [empty("calendar", "Nothing planned", "Tasks with a due date show up here.")];
  }
  const groups = new Map();
  for (const task of tasks) {
    const name = view.kind === "today" ? (task.due < today() ? "Overdue" : "Today") : plannedGroup(task.due);
    if (!groups.has(name)) groups.set(name, []);
    groups.get(name).push(task);
  }
  return [...groups].map(([name, items]) => group(name, items, { showList: true, tone: name === "Overdue" ? "overdue" : "" }));
}

function group(name, tasks, options) {
  return h(
    "section",
    { class: `group ${options.tone ?? ""}` },
    h("h2", { class: "group-title" }, name, h("span", { class: "count" }, tasks.length)),
    h("ul", { class: "tasks" }, tasks.map((task) => row(task, options))),
  );
}

function completedSection(done) {
  const list = currentList();
  return h(
    "section",
    { class: "group completed" },
    h(
      "div",
      { class: "group-title" },
      h(
        "button",
        {
          class: `disclosure${showDone ? " open" : ""}`,
          "aria-expanded": String(showDone),
          onclick: () => {
            showDone = !showDone;
            render();
          },
        },
        icon("chevron"),
        "Completed",
        h("span", { class: "count" }, done.length),
      ),
      showDone && h("button", { class: "link-button", onclick: () => clearCompleted(list) }, "Clear"),
    ),
    showDone && h("ul", { class: "tasks" }, done.map((task) => row(task, {}))),
  );
}

function empty(iconName, title, text) {
  return h("div", { class: "empty" }, h("div", { class: "empty-art" }, icon(iconName)), h("h2", null, title), h("p", null, text));
}

function row(task, { sortable = false, showList = false, query = "" }) {
  const list = listById(task.list_id);
  const meta = [];
  if (task.due) {
    const { text, tone } = dueLabel(task.due);
    meta.push(h("span", { class: `meta due ${task.done ? "" : tone}` }, icon("calendar"), text));
  }
  if (task.notes) meta.push(h("span", { class: "meta", title: "Has notes" }, icon("notes"), query ? highlight(snippet(task.notes, query), query) : null));
  if (showList && list) meta.push(h("span", { class: "meta list-name" }, h("span", { class: `dot c-${list.color}` }), list.name));

  const element = h(
    "li",
    {
      class: `task${task.done ? " done" : ""}${task.id === state.selectedId ? " selected" : ""}`,
      dataset: { id: task.id },
      draggable: sortable ? "true" : null,
      style: `--list-color: var(--c-${list?.color ?? "gray"})`,
      onclick: () => select(task.id, { open: true }),
    },
    sortable && h("span", { class: "grip", title: "Drag to reorder" }, icon("grip")),
    h(
      "button",
      {
        class: "check",
        role: "checkbox",
        "aria-checked": String(task.done),
        "aria-label": task.done ? "Mark as not done" : "Mark as done",
        onclick: (event) => {
          event.stopPropagation();
          toggleAnimated(task.id);
        },
      },
      icon("check"),
    ),
    h("div", { class: "task-body" }, h("span", { class: "task-title" }, highlight(task.title, query)), meta.length > 0 && h("div", { class: "task-meta" }, meta)),
  );
  return element;
}

function snippet(notes, query) {
  const at = notes.toLowerCase().indexOf(query.toLowerCase());
  if (at === -1) return "";
  const start = Math.max(0, at - 20);
  return (start ? "…" : "") + notes.slice(start, at + query.length + 30);
}

/** Ticks the box with a little animation, then moves the task. */
export function toggleAnimated(id) {
  const element = document.querySelector(`#task-area li[data-id="${id}"]`);
  const task = visibleTasks().find((each) => each.id === id);
  if (!element || !task) return toggleTask(id);
  element.classList.add(task.done ? "unchecking" : "checking");
  setTimeout(() => toggleTask(id), matchMedia("(prefers-reduced-motion: reduce)").matches ? 0 : 320);
}

/** The ids of the tasks on screen, top to bottom (for J/K). */
export const orderOnScreen = () => [...document.querySelectorAll("#task-area li[data-id]")].map((row) => row.dataset.id);

export function markSelected() {
  for (const element of document.querySelectorAll("#task-area li[data-id]")) {
    const selected = element.dataset.id === state.selectedId;
    element.classList.toggle("selected", selected);
    if (selected) element.scrollIntoView({ block: "nearest" });
  }
}

// --- The list's menu ------------------------------------------------------------

function listMenu(anchor) {
  const list = currentList();
  if (!list) return;
  const swatches = h(
    "div",
    { class: "swatches", role: "group", "aria-label": "Color" },
    COLORS.map((color) =>
      h("button", {
        class: `swatch c-${color}${color === list.color ? " current" : ""}`,
        title: color,
        "aria-label": color,
        onclick: () => {
          closeMenu();
          updateList(list.id, { color });
        },
      }),
    ),
  );
  menu(anchor, [
    { label: "Rename", icon: "edit", onSelect: () => rename(list) },
    swatches,
    { label: "Clear completed", icon: "check", onSelect: () => clearCompleted(list) },
    { label: "Delete list", icon: "trash", danger: true, onSelect: () => removeList(list) },
  ]);
}

function rename(list) {
  const heading = $("#view-title");
  const input = h("input", { class: "title-input", value: list.name, maxlength: 60, "aria-label": "List name" });
  heading.replaceChildren(input);
  input.select();
  let done = false;
  const finish = (save) => {
    if (done) return;
    done = true;
    const name = input.value.trim();
    if (save && name && name !== list.name) updateList(list.id, { name });
    render();
  };
  input.addEventListener("keydown", (event) => {
    if (event.key === "Enter") finish(true);
    if (event.key === "Escape") finish(false);
  });
  input.addEventListener("blur", () => finish(true));
}

async function clearCompleted(list) {
  try {
    const deleted = await clearDone(list.id);
    toast(deleted ? `Cleared ${deleted} completed task${deleted > 1 ? "s" : ""}` : "Nothing to clear");
  } catch (error) {
    toast(error.message, { tone: "error" });
  }
}

async function removeList(list) {
  if (state.lists.length === 1) return toast("Your last list stays: rename it instead.", { tone: "error" });
  const count = list.open_tasks;
  const ok = await confirm({
    title: `Delete “${list.name}”?`,
    message: count ? `Its ${count} open task${count > 1 ? "s" : ""} will be deleted too. This can't be undone.` : "This can't be undone.",
    confirmLabel: "Delete list",
    danger: true,
  });
  if (!ok) return;
  try {
    await deleteList(list.id);
    toast(`Deleted “${list.name}”`);
  } catch (error) {
    toast(error.message, { tone: "error" });
  }
}
