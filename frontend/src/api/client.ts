import type { ApiErrorBody } from "./types";

const TOKEN_KEY = "medialab.token";

function readToken(): string {
  const url = new URL(window.location.href);
  const fromUrl = url.searchParams.get("token");
  if (fromUrl) {
    try {
      sessionStorage.setItem(TOKEN_KEY, fromUrl);
    } catch {
      /* storage may be unavailable */
    }
    url.searchParams.delete("token");
    window.history.replaceState(null, "", url.pathname + url.search + url.hash);
    return fromUrl;
  }
  try {
    const stored = sessionStorage.getItem(TOKEN_KEY);
    if (stored) return stored;
  } catch {
    /* ignore */
  }
  return import.meta.env.DEV ? "dev" : "";
}

export const token = readToken();

export class ApiError extends Error implements ApiErrorBody {
  code: string;
  hint?: string;
  retryable?: boolean;
  detail?: string;
  extra?: Record<string, unknown>;
  status: number;

  constructor(body: ApiErrorBody, status: number) {
    super(body.message || "Request failed");
    this.code = body.code || "unknown";
    this.hint = body.hint;
    this.retryable = body.retryable;
    this.detail = body.detail;
    this.extra = body.extra;
    this.status = status;
  }
}

async function request<T>(method: string, path: string, body?: unknown, init?: RequestInit): Promise<T> {
  const headers: Record<string, string> = { "X-MediaLab-Token": token };
  let payload: BodyInit | undefined;
  if (body instanceof FormData) payload = body;
  else if (body !== undefined) {
    headers["Content-Type"] = "application/json";
    payload = JSON.stringify(body);
  }
  let res: Response;
  try {
    res = await fetch(path, { method, headers, body: payload, ...init });
  } catch (e) {
    throw new ApiError(
      {
        code: "backend_unreachable",
        message: "Media Lab's engine is not responding.",
        hint: "Restart the application. If it keeps happening, check the logs folder.",
        detail: String(e),
      },
      0,
    );
  }
  const text = await res.text();
  let data: any = null;
  try {
    data = text ? JSON.parse(text) : null;
  } catch {
    data = { message: text };
  }
  if (!res.ok) {
    if (data && typeof data === "object" && "code" in data) throw new ApiError(data, res.status);
    throw new ApiError(
      { code: `http_${res.status}`, message: data?.message || data?.detail || res.statusText || "Request failed" },
      res.status,
    );
  }
  return data as T;
}

export const api = {
  get: <T>(p: string) => request<T>("GET", p),
  post: <T>(p: string, b?: unknown) => request<T>("POST", p, b ?? {}),
  put: <T>(p: string, b?: unknown) => request<T>("PUT", p, b ?? {}),
  patch: <T>(p: string, b?: unknown) => request<T>("PATCH", p, b ?? {}),
  del: <T>(p: string) => request<T>("DELETE", p),
  upload: <T>(p: string, form: FormData) => request<T>("POST", p, form),
};

/** URL for an <img>/<video> served by the API (token in the query string). */
export function mediaUrl(path: string): string {
  const sep = path.includes("?") ? "&" : "?";
  return `${path}${sep}token=${encodeURIComponent(token)}`;
}

export function thumbUrl(key?: string | null): string | undefined {
  return key ? mediaUrl(`/api/thumb/${key}`) : undefined;
}

export function localMediaUrl(filePath: string): string {
  return mediaUrl(`/api/media?path=${encodeURIComponent(filePath)}`);
}
