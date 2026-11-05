// The JSON API, as the server describes it at /docs.

export class ApiError extends Error {
  constructor(status, data) {
    super(describe(status, data));
    this.status = status;
    this.data = data;
  }
}

function describe(status, data) {
  if (Array.isArray(data?.detail)) return data.detail.map((problem) => problem.msg).join(" · ");
  if (data?.error) return data.error;
  if (status === 0) return "Can't reach the server. Are you offline?";
  if (status === 429) return "That's a lot of requests: wait a moment.";
  return `Something went wrong (${status}).`;
}

let onUnauthorized = () => {};

/** What to do when the session has ended (signed out elsewhere, expired). */
export function whenSignedOut(callback) {
  onUnauthorized = callback;
}

async function request(method, path, body, { keepalive = false } = {}) {
  const options = { method, headers: {}, keepalive };
  if (body !== undefined) {
    options.headers["Content-Type"] = "application/json";
    options.body = JSON.stringify(body);
  }
  let response;
  try {
    response = await fetch(path, options);
  } catch {
    throw new ApiError(0, null);
  }
  const data = response.status === 204 ? null : await response.json().catch(() => null);
  if (response.status === 401) onUnauthorized();
  if (!response.ok) throw new ApiError(response.status, data);
  return data;
}

const query = (params) => {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) if (value != null && value !== "") search.set(key, value);
  return search.toString();
};

export const api = {
  me: () => request("GET", "/api/me"),

  lists: () => request("GET", "/api/lists"),
  createList: (fields) => request("POST", "/api/lists", fields),
  updateList: (id, changes) => request("PATCH", `/api/lists/${id}`, changes),
  deleteList: (id) => request("DELETE", `/api/lists/${id}`),
  reorder: (listId, taskIds) => request("PUT", `/api/lists/${listId}/order`, { task_ids: taskIds }),
  clearDone: (listId) => request("POST", `/api/lists/${listId}/clear-done`),

  tasks: (filters) => request("GET", `/api/tasks?${query(filters)}`),
  createTask: (fields) => request("POST", "/api/tasks", fields),
  updateTask: (id, changes) => request("PATCH", `/api/tasks/${id}`, changes),
  deleteTask: (id, options) => request("DELETE", `/api/tasks/${id}`, undefined, options),
};
