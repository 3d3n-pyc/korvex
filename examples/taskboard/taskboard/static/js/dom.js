// Building the page from JavaScript, safely: text always goes in as text
// nodes, never as HTML, so a task title can't inject markup.

const SVG = "http://www.w3.org/2000/svg";
const PROPERTIES = new Set(["value", "checked", "disabled", "selected"]);

/** h("button", { class: "x", onclick: fn }, "Label", child, ...) */
export function h(tag, props, ...children) {
  const element = document.createElement(tag);
  for (const [key, value] of Object.entries(props ?? {})) {
    if (value == null || value === false) continue;
    if (key === "class") element.className = value;
    else if (key === "dataset") Object.assign(element.dataset, value);
    else if (key.startsWith("on")) element.addEventListener(key.slice(2), value);
    else if (PROPERTIES.has(key)) element[key] = value;
    else element.setAttribute(key, value === true ? "" : String(value));
  }
  append(element, children);
  return element;
}

export function append(element, children) {
  for (const child of [children].flat(Infinity)) {
    if (child == null || child === false || child === "") continue;
    element.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return element;
}

/** An icon from the sprite in index.html. */
export function icon(name, className = "icon") {
  const svg = document.createElementNS(SVG, "svg");
  svg.setAttribute("class", className);
  svg.setAttribute("aria-hidden", "true");
  const use = document.createElementNS(SVG, "use");
  use.setAttribute("href", `#i-${name}`);
  svg.append(use);
  return svg;
}

export const $ = (selector, root = document) => root.querySelector(selector);

export function debounce(fn, ms) {
  let timer;
  const debounced = (...args) => {
    clearTimeout(timer);
    timer = setTimeout(() => fn(...args), ms);
  };
  debounced.flush = (...args) => {
    clearTimeout(timer);
    fn(...args);
  };
  return debounced;
}

/** `text` with every match of `query` wrapped in <mark>, as nodes. */
export function highlight(text, query) {
  if (!query) return text;
  const parts = [];
  const lower = text.toLowerCase();
  const needle = query.toLowerCase();
  let start = 0;
  for (let at = lower.indexOf(needle); at !== -1; at = lower.indexOf(needle, start)) {
    parts.push(text.slice(start, at), h("mark", null, text.slice(at, at + needle.length)));
    start = at + needle.length;
  }
  parts.push(text.slice(start));
  return parts;
}

/** Grows a textarea with its content. */
export function autosize(textarea) {
  const fit = () => {
    textarea.style.height = "auto";
    textarea.style.height = `${textarea.scrollHeight}px`;
  };
  textarea.addEventListener("input", fit);
  requestAnimationFrame(fit);
  return fit;
}
