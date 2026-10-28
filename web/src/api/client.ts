// The API client. Same origin only; cookies carry the visitor's session. Every write sends the
// X-Kaskazi header, which a form on another site can't add.

export class ApiError extends Error {
  readonly status: number;
  readonly fields: Record<string, string>;

  constructor(status: number, message: string, fields: Record<string, string> = {}) {
    super(message);
    this.status = status;
    this.fields = fields;
  }
}

const FALLBACK: Record<number, string> = {
  401: "Choose a sample customer to start.",
  403: "That isn't allowed here.",
  404: "That couldn't be found.",
  409: "That has already changed. Have a look and try again.",
  429: "That was a lot of requests at once. Please wait a moment and try again.",
  502: "Kaskazi is briefly unavailable. Please try again in a minute.",
  503: "Kaskazi is briefly unavailable. Please try again in a minute.",
  504: "Kaskazi is briefly unavailable. Please try again in a minute.",
};

async function readError(response: Response): Promise<ApiError> {
  // nginx and Cloudflare can answer instead of the API; never assume JSON.
  let message = FALLBACK[response.status] ?? "Something went wrong. Please try again.";
  let fields: Record<string, string> = {};
  try {
    const body: unknown = await response.json();
    if (body && typeof body === "object") {
      const b = body as { detail?: unknown; title?: unknown; fields?: Record<string, string> };
      if (typeof b.detail === "string") message = b.detail;
      else if (typeof b.title === "string") message = b.title;
      if (b.fields) fields = b.fields;
    }
  } catch {
    // keep the fallback message
  }
  return new ApiError(response.status, message, fields);
}

async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  let response: Response;
  try {
    response = await fetch(path, {
      method,
      credentials: "same-origin",
      headers: {
        Accept: "application/json",
        ...(body !== undefined ? { "Content-Type": "application/json" } : {}),
        ...(method !== "GET" ? { "X-Kaskazi": "1" } : {}),
      },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  } catch {
    throw new ApiError(0, "You seem to be offline. Check your connection and try again.");
  }
  if (!response.ok) throw await readError(response);
  return (await response.json()) as T;
}

export const api = {
  get: <T,>(path: string) => request<T>("GET", path),
  post: <T,>(path: string, body: unknown = {}) => request<T>("POST", path, body),
  del: <T,>(path: string) => request<T>("DELETE", path),
};
