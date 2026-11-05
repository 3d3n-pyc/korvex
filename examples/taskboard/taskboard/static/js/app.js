// Starts the page: the sign-in screen, or the app with its live updates
// and keyboard shortcuts.

import { api, whenSignedOut } from "./api.js";
import { today } from "./dates.js";
import { $ } from "./dom.js";
import { connect } from "./live.js";
import { install, SHORTCUTS } from "./shortcuts.js";
import {
  changed,
  closeDetails,
  go,
  hashToView,
  nudgeSelected,
  onChange,
  refresh,
  refreshSoon,
  select,
  setErrorReporter,
  state,
  taskById,
  updateTask,
} from "./store.js";
import * as details from "./ui/details.js";
import { closeMenu, showShortcuts, toast } from "./ui/overlays.js";
import * as sidebar from "./ui/sidebar.js";
import * as tasks from "./ui/tasks.js";

async function start() {
  const { user } = await api.me();
  if (!user) return showSignIn();

  $("#app").hidden = false;
  setErrorReporter((error) => toast(error.message, { tone: "error" }));
  whenSignedOut(() => location.assign("/"));
  sidebar.setup();
  tasks.setup();
  onChange(() => {
    sidebar.render();
    tasks.render();
    details.render();
  });
  setupMobile();

  await refresh();
  const view = hashToView(location.hash) ?? { kind: "list", id: state.lists[0].id };
  await go(view, { replace: true });
  addEventListener("popstate", () => go(hashToView(location.hash) ?? { kind: "today" }, { replace: true }));

  connect({
    onEvent: () => refreshSoon(),
    onStatus: (status) => {
      state.live = { ...state.live, ...status };
      sidebar.render();
    },
  });
  install(shortcutActions());
  $("#help-button").addEventListener("click", () => showShortcuts(SHORTCUTS));
}

function showSignIn() {
  $("#login").hidden = false;
  const failed = new URLSearchParams(location.search).has("login");
  $("#login-failed").hidden = !failed;
  if (failed) {
    $(".login-card").classList.add("shake");
    history.replaceState(null, "", "/"); // a reload shouldn't show the error again
  }
}

function setupMobile() {
  const close = () => document.body.classList.remove("sidebar-open");
  $("#menu-toggle").addEventListener("click", () => document.body.classList.add("sidebar-open"));
  $("#sidebar-backdrop").addEventListener("click", close);
}

function shortcutActions() {
  const move = (step) => {
    const ids = tasks.orderOnScreen();
    if (!ids.length) return;
    const at = ids.indexOf(state.selectedId);
    const next = ids[at === -1 ? (step > 0 ? 0 : ids.length - 1) : Math.max(0, Math.min(ids.length - 1, at + step))];
    state.selectedId = next;
    if (state.detailsOpen) changed();
    else tasks.markSelected();
  };
  const selected = () => (taskById(state.selectedId) ? state.selectedId : null);
  return {
    newTask: () => {
      if (state.view.kind === "search") go({ kind: "list", id: state.lists[0].id }).then(tasks.focusNewTask);
      else tasks.focusNewTask();
    },
    search: () => $("#search").focus(),
    next: () => move(1),
    previous: () => move(-1),
    toggle: () => selected() && tasks.toggleAnimated(selected()),
    open: () => {
      if (!selected()) return;
      select(selected(), { open: true });
      details.focusTitle();
    },
    dueToday: () => selected() && updateTask(selected(), { due: today() }),
    moveUp: () => nudgeSelected(-1),
    moveDown: () => nudgeSelected(1),
    remove: () => selected() && details.removeWithUndo(selected()),
    today: () => go({ kind: "today" }),
    planned: () => go({ kind: "planned" }),
    list: (index) => state.lists[index] && go({ kind: "list", id: state.lists[index].id }),
    help: () => showShortcuts(SHORTCUTS),
    escape: (event) => {
      const field = event.target.closest?.("input, textarea, select");
      if (closeMenu()) return;
      if (field?.id === "search") return sidebar.leaveSearch();
      if (field) return field.blur();
      if (document.body.classList.contains("sidebar-open")) return document.body.classList.remove("sidebar-open");
      if (state.detailsOpen) return closeDetails();
      if (state.selectedId) {
        state.selectedId = null;
        tasks.markSelected();
      }
    },
  };
}

start().catch((error) => {
  console.error(error);
  toast("The app couldn't start. Is the server running?", { tone: "error", duration: 10_000 });
});
