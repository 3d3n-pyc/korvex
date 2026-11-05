// The panel on the right: one task, every field editable. Fields save on
// their own (titles on Enter or when leaving, notes as you pause typing).

import { fullDate, nextMonday } from "../dates.js";
import { $, append, autosize, debounce, h, icon } from "../dom.js";
import { closeDetails, deleteTask, dueIn, moveTask, state, taskById, updateTask } from "../store.js";
import { toast } from "./overlays.js";
import { toggleAnimated } from "./tasks.js";

let shownId = null;
let fields = null;

export function render() {
  const panel = $("#details");
  const task = state.detailsOpen ? taskById(state.selectedId) : null;
  document.body.classList.toggle("details-open", Boolean(task));
  if (!task) {
    panel.hidden = true;
    shownId = null;
    return;
  }
  panel.hidden = false;
  if (task.id !== shownId) build(panel, task);
  else refresh(task);
}

function build(panel, task) {
  shownId = task.id;
  const title = h("textarea", { class: "details-title", rows: 1, maxlength: 200, "aria-label": "Title" });
  const notes = h("textarea", { class: "details-notes", placeholder: "Add notes", maxlength: 5000, "aria-label": "Notes" });
  const date = h("input", { type: "date", class: "date-input", "aria-label": "Due date" });
  const list = h("select", { class: "select", "aria-label": "List" });
  const check = h("button", { class: "check big", role: "checkbox", onclick: () => toggleAnimated(task.id) }, icon("check"));
  const due = h("div", { class: "due-choices" });
  const footer = h("p", { class: "details-dates" });
  fields = { title, notes, date, list, check, due, footer };

  const saveTitle = () => {
    const value = title.value.replace(/\s+/g, " ").trim();
    const current = taskById(shownId);
    if (!current) return;
    if (!value) title.value = current.title; // a title can't be blank: put it back
    else if (value !== current.title) updateTask(current.id, { title: value });
  };
  title.addEventListener("keydown", (event) => {
    if (event.key === "Enter") {
      event.preventDefault();
      title.blur();
    }
    if (event.key === "Escape") {
      title.value = taskById(shownId)?.title ?? "";
      event.stopPropagation();
      title.blur();
    }
  });
  title.addEventListener("blur", saveTitle);

  const saveNotes = debounce(() => {
    const current = taskById(shownId);
    if (current && notes.value !== current.notes) updateTask(current.id, { notes: notes.value });
  }, 600);
  notes.addEventListener("input", saveNotes);
  notes.addEventListener("blur", () => saveNotes.flush());

  date.addEventListener("change", () => updateTask(shownId, { due: date.value || null }));
  list.addEventListener("change", () => moveTask(shownId, list.value));

  panel.replaceChildren(
    h(
      "header",
      { class: "details-header" },
      h("span", { class: "details-kicker" }, "Task"),
      h("button", { class: "icon-button", title: "Close (Esc)", "aria-label": "Close", onclick: closeDetails }, icon("x")),
    ),
    h("div", { class: "details-main" }, check, title),
    h("section", { class: "field" }, h("h3", null, icon("calendar"), "Due"), due, date),
    h("section", { class: "field" }, h("h3", null, icon("menu"), "List"), list),
    h("section", { class: "field grow" }, h("h3", null, icon("notes"), "Notes"), notes),
    h(
      "footer",
      { class: "details-footer" },
      footer,
      h(
        "button",
        {
          class: "icon-button danger",
          title: "Delete (Del)",
          "aria-label": "Delete task",
          onclick: () => removeWithUndo(shownId),
        },
        icon("trash"),
      ),
    ),
  );
  refresh(task, { force: true });
  autosize(title);
  autosize(notes);
}

/** Puts the task's values in the fields, except the one being edited. */
function refresh(task, { force = false } = {}) {
  const { title, notes, date, list, check, due, footer } = fields;
  const editing = (field) => !force && document.activeElement === field;
  if (!editing(title)) title.value = task.title;
  if (!editing(notes)) notes.value = task.notes;
  date.value = task.due ?? "";
  check.setAttribute("aria-checked", String(task.done));
  check.setAttribute("aria-label", task.done ? "Mark as not done" : "Mark as done");
  $("#details").classList.toggle("done", task.done);
  $("#details").style.setProperty("--list-color", `var(--c-${state.lists.find((each) => each.id === task.list_id)?.color ?? "gray"})`);

  list.replaceChildren(...state.lists.map((each) => h("option", { value: each.id, selected: each.id === task.list_id }, each.name)));

  const choices = [
    ["Today", dueIn(0)],
    ["Tomorrow", dueIn(1)],
    ["Next week", nextMonday()],
  ];
  due.replaceChildren();
  append(due, [
    ...choices.map(([label, day]) =>
      h("button", { class: `chip${task.due === day ? " set" : ""}`, onclick: () => updateTask(task.id, { due: day }) }, label),
    ),
    task.due && h("button", { class: "chip clear", title: "Remove the due date", onclick: () => updateTask(task.id, { due: null }) }, icon("x"), "Clear"),
  ]);

  footer.textContent = task.done && task.completed_at ? `Completed ${fullDate(task.completed_at)}` : `Created ${fullDate(task.created_at)}`;
}

export function focusTitle() {
  fields?.title.focus();
}

/** Deletes with a toast to undo it. */
export function removeWithUndo(id) {
  const task = taskById(id);
  if (!task) return;
  const undo = deleteTask(id);
  toast(`Deleted “${task.title}”`, { action: "Undo", onAction: undo, duration: 5000 });
}
