// Drag and drop with the browser's own API: rows move as you drag, and
// dropping one on a list in the sidebar moves the task there.

const TYPE = "application/x-taskboard-task";
let dragged = null;
let droppedElsewhere = false;

/** Rows (`li[draggable]`) in `container` can be dragged into a new order. */
export function sortable(container, onReorder) {
  let before = [];
  container.addEventListener("dragstart", (event) => {
    const row = event.target.closest("li[draggable=true]");
    if (!row) return;
    dragged = row;
    droppedElsewhere = false;
    before = ids(container);
    event.dataTransfer.effectAllowed = "move";
    event.dataTransfer.setData(TYPE, row.dataset.id);
    requestAnimationFrame(() => row.classList.add("dragging")); // after the browser took its snapshot
  });
  container.addEventListener("dragover", (event) => {
    if (!dragged || !container.contains(dragged)) return;
    event.preventDefault();
    const next = rowBelow(container, event.clientY);
    if (next !== dragged.nextElementSibling) container.insertBefore(dragged, next);
  });
  container.addEventListener("dragend", () => {
    if (!dragged) return;
    dragged.classList.remove("dragging");
    const after = ids(container);
    const moved = after.join() !== before.join();
    dragged = null;
    if (droppedElsewhere) return;
    if (moved) onReorder(after);
  });
}

/** `element` (a list in the sidebar) accepts dropped tasks. */
export function dropTarget(element, onDrop) {
  element.addEventListener("dragover", (event) => {
    if (!event.dataTransfer.types.includes(TYPE)) return;
    event.preventDefault();
    element.classList.add("drop-here");
  });
  element.addEventListener("dragleave", () => element.classList.remove("drop-here"));
  element.addEventListener("drop", (event) => {
    element.classList.remove("drop-here");
    const id = event.dataTransfer.getData(TYPE);
    if (!id) return;
    event.preventDefault();
    droppedElsewhere = true;
    onDrop(id);
  });
}

const ids = (container) => [...container.querySelectorAll(":scope > li[data-id]")].map((row) => row.dataset.id);

function rowBelow(container, y) {
  for (const row of container.querySelectorAll(":scope > li[data-id]:not(.dragging)")) {
    const box = row.getBoundingClientRect();
    if (y < box.top + box.height / 2) return row;
  }
  return null;
}
