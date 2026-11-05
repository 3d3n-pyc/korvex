// Things that float above the page: toasts, dialogs, menus.

import { $, h, icon } from "../dom.js";

// --- Toasts -------------------------------------------------------------------

/** A short message at the bottom; with `action`, a button (Undo...). */
export function toast(message, { action, onAction, tone = "info", duration = 4000 } = {}) {
  const close = () => {
    element.classList.add("leaving");
    setTimeout(() => element.remove(), 200);
  };
  const element = h(
    "div",
    { class: `toast ${tone}`, role: tone === "error" ? "alert" : "status" },
    h("span", null, message),
    action &&
      h("button", {
        class: "toast-action",
        onclick: () => {
          onAction();
          close();
        },
      }, action),
  );
  $("#toasts").append(element);
  setTimeout(close, duration);
  return close;
}

// --- Dialogs ------------------------------------------------------------------

function dialog(className, ...content) {
  const element = h("dialog", { class: `dialog ${className}` }, ...content);
  element.addEventListener("close", () => element.remove());
  element.addEventListener("click", (event) => event.target === element && element.close()); // the backdrop
  document.body.append(element);
  element.showModal();
  return element;
}

/** Resolves to true if the user confirms. */
export function confirm({ title, message, confirmLabel, danger = false }) {
  return new Promise((resolve) => {
    const element = dialog(
      "confirm",
      h("h2", null, title),
      h("p", null, message),
      h(
        "form",
        { method: "dialog", class: "dialog-actions" },
        h("button", { class: "button", value: "cancel" }, "Cancel"),
        h("button", { class: `button ${danger ? "danger" : "primary"}`, value: "ok", autofocus: true }, confirmLabel),
      ),
    );
    element.addEventListener("close", () => resolve(element.returnValue === "ok"));
  });
}

export function showShortcuts(shortcuts) {
  dialog(
    "shortcuts",
    h("h2", null, "Keyboard shortcuts"),
    h(
      "dl",
      { class: "shortcut-list" },
      shortcuts.map(([keys, label]) => [
        h("dt", null, keys.map((key) => h("kbd", null, key))),
        h("dd", null, label),
      ]),
    ),
    h("form", { method: "dialog", class: "dialog-actions" }, h("button", { class: "button primary" }, "Got it")),
  );
}

// --- Menus --------------------------------------------------------------------

let openMenu = null;

/** A menu under `anchor`. Items: {label, icon, onSelect, danger} or a node. */
export function menu(anchor, items) {
  closeMenu();
  const element = h(
    "div",
    { class: "menu", role: "menu" },
    items.map((item) =>
      item instanceof Node
        ? item
        : h(
            "button",
            {
              class: `menu-item${item.danger ? " danger" : ""}`,
              role: "menuitem",
              onclick: () => {
                closeMenu();
                item.onSelect();
              },
            },
            icon(item.icon),
            item.label,
          ),
    ),
  );
  document.body.append(element);
  const box = anchor.getBoundingClientRect();
  element.style.top = `${box.bottom + 6}px`;
  element.style.left = `${Math.max(8, Math.min(box.right - element.offsetWidth, innerWidth - element.offsetWidth - 8))}px`;
  openMenu = element;
  element.querySelector("button")?.focus();
  setTimeout(() => document.addEventListener("pointerdown", outside), 0);
  return element;
}

function outside(event) {
  if (openMenu && !openMenu.contains(event.target)) closeMenu();
}

export function closeMenu() {
  if (!openMenu) return false;
  openMenu.remove();
  openMenu = null;
  document.removeEventListener("pointerdown", outside);
  return true;
}
