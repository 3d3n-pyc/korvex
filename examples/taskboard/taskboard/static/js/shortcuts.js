// Keyboard shortcuts. They don't fire while typing in a field, except
// Escape, which leaves it.

export const SHORTCUTS = [
  [["N"], "New task", "newTask"],
  [["/"], "Search", "search"],
  [["J"], "Next task", "next"],
  [["K"], "Previous task", "previous"],
  [["X"], "Done / not done", "toggle"],
  [["Enter"], "Open the task", "open"],
  [["T"], "Due today", "dueToday"],
  [["Alt", "↑"], "Move up", "moveUp"],
  [["Alt", "↓"], "Move down", "moveDown"],
  [["Del"], "Delete (with undo)", "remove"],
  [["1"], "Today", "today"],
  [["2"], "Planned", "planned"],
  [["3–9"], "Your lists", null],
  [["?"], "These shortcuts", "help"],
  [["Esc"], "Close, cancel", null],
];

const KEYS = {
  n: "newTask",
  "/": "search",
  j: "next",
  ArrowDown: "next",
  k: "previous",
  ArrowUp: "previous",
  x: "toggle",
  " ": "toggle",
  Enter: "open",
  t: "dueToday",
  "alt+ArrowUp": "moveUp",
  "alt+ArrowDown": "moveDown",
  Delete: "remove",
  Backspace: "remove",
  "1": "today",
  "2": "planned",
  "?": "help",
};

/** `actions` maps names (above) to functions; `list(n)` opens the nth list. */
export function install(actions) {
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") return actions.escape(event);
    if (event.ctrlKey || event.metaKey || event.isComposing) return;
    if (event.target.closest("input, textarea, select, [contenteditable], dialog")) return;
    if ((event.key === "Enter" || event.key === " ") && event.target.closest("button, a, summary")) return; // their own click

    const key = event.altKey ? `alt+${event.key}` : event.key;
    const name = KEYS[key] ?? KEYS[key.toLowerCase()];
    if (name && actions[name]) {
      event.preventDefault();
      actions[name]();
    } else if (/^[3-9]$/.test(event.key)) {
      event.preventDefault();
      actions.list(Number(event.key) - 3);
    }
  });
}
