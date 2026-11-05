// The sidebar: search, the smart views, the lists.

import { $, debounce, h, icon } from "../dom.js";
import { dropTarget } from "../dragdrop.js";
import { createList, go, moveTask, state, viewToHash } from "../store.js";
import { toast } from "./overlays.js";

let beforeSearch = null; // where to go back to when the search is cleared

export function setup() {
  const search = $("#search");
  const searchFor = debounce((q) => {
    if (state.view.kind !== "search") beforeSearch = state.view;
    go({ kind: "search", q }, { replace: state.view.kind === "search" });
  }, 150);
  search.addEventListener("input", () => {
    if (search.value.trim()) searchFor(search.value.trim());
    else leaveSearch();
  });
  setupNewList();
}

/** Clears the search and goes back to where it started. */
export function leaveSearch() {
  const search = $("#search");
  search.value = "";
  search.blur();
  if (state.view.kind === "search") go(beforeSearch ?? { kind: "today" }, { replace: true });
}

function setupNewList() {
  const form = $("#new-list");
  const input = form.elements.name;
  $("#new-list-button").addEventListener("click", () => {
    form.hidden = false;
    input.focus();
  });
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const name = input.value.trim();
    if (!name) return;
    input.value = "";
    form.hidden = true;
    await createList(name).catch((error) => toast(error.message, { tone: "error" }));
  });
  input.addEventListener("blur", () => {
    if (!input.value.trim()) form.hidden = true;
  });
  input.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      input.value = "";
      input.blur();
    }
  });
}

export function render() {
  const current = viewToHash(state.view);
  const item = (view, label, iconOrDot, count, extra = {}) =>
    h(
      "li",
      null,
      h(
        "a",
        {
          href: viewToHash(view),
          class: `nav-item${viewToHash(view) === current ? " active" : ""}`,
          "aria-current": viewToHash(view) === current ? "page" : null,
          onclick: (event) => {
            event.preventDefault();
            document.body.classList.remove("sidebar-open");
            go(view);
          },
          ...extra,
        },
        iconOrDot,
        h("span", { class: "nav-label" }, label),
        count ? h("span", { class: "count" }, count) : null,
      ),
    );

  $("#smart-views").replaceChildren(
    item({ kind: "today" }, "Today", icon("sun", "icon today-icon"), state.counts.today),
    item({ kind: "planned" }, "Planned", icon("calendar", "icon planned-icon"), state.counts.planned),
  );

  $("#lists").replaceChildren(
    ...state.lists.map((list) => {
      const li = item({ kind: "list", id: list.id }, list.name, h("span", { class: `dot c-${list.color}` }), list.open_tasks);
      dropTarget(li, (taskId) => moveTask(taskId, list.id));
      return li;
    }),
  );

  const search = $("#search");
  if (state.view.kind !== "search" && document.activeElement !== search) search.value = "";

  const live = $("#live");
  live.classList.toggle("on", state.live.connected);
  $("#live-text").textContent = !state.live.connected
    ? "Offline, retrying…"
    : state.live.pages > 1
      ? `Live · ${state.live.pages} windows`
      : "Live";
}
