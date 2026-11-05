// Due dates are calendar days ("2026-11-05"), in the user's own time zone:
// "today" is whatever day it is where they are, so the page works it out
// and sends it to the API (?due_to=...), never the other way around.

const LOCALE = "en";
const pad = (n) => String(n).padStart(2, "0");

export const iso = (date) => `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`;
export const today = () => iso(new Date());

export function parse(day) {
  const [year, month, date] = day.split("-").map(Number);
  return new Date(year, month - 1, date);
}

export function addDays(day, days) {
  const date = parse(day);
  date.setDate(date.getDate() + days);
  return iso(date);
}

/** Days from today to `day`: -1 yesterday, 0 today, 1 tomorrow. */
export const daysAway = (day) => Math.round((parse(day) - parse(today())) / 86_400_000);

export function nextMonday() {
  const date = new Date();
  date.setDate(date.getDate() + (((8 - date.getDay()) % 7) || 7));
  return iso(date);
}

function short(date) {
  const options = { month: "short", day: "numeric" };
  if (date.getFullYear() !== new Date().getFullYear()) options.year = "numeric";
  return date.toLocaleDateString(LOCALE, options);
}

/** How a due date reads, and how urgent it looks. */
export function dueLabel(day) {
  const days = daysAway(day);
  const date = parse(day);
  if (days === 0) return { text: "Today", tone: "today" };
  if (days === 1) return { text: "Tomorrow", tone: "soon" };
  if (days === -1) return { text: "Yesterday", tone: "overdue" };
  if (days < 0) return { text: short(date), tone: "overdue" };
  if (days < 7) return { text: date.toLocaleDateString(LOCALE, { weekday: "long" }), tone: "soon" };
  return { text: short(date), tone: "later" };
}

export function plannedGroup(day) {
  const days = daysAway(day);
  if (days < 0) return "Overdue";
  if (days === 0) return "Today";
  if (days === 1) return "Tomorrow";
  if (days < 7) return "This week";
  return "Later";
}

export const longDate = (date = new Date()) =>
  date.toLocaleDateString(LOCALE, { weekday: "long", month: "long", day: "numeric" });

export const fullDate = (timestamp) =>
  new Date(timestamp).toLocaleDateString(LOCALE, { month: "short", day: "numeric", year: "numeric" });
