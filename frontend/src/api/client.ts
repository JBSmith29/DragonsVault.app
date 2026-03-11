const trimTrailingSlash = (value: string): string => value.replace(/\/+$/, "");

export const API_BASE = trimTrailingSlash(import.meta.env.VITE_API_BASE_URL ?? "/api");

export const apiUrl = (path: string): string => `${API_BASE}${path}`;

const requestCredentials = (): RequestCredentials => {
  if (typeof window === "undefined") {
    return "include";
  }
  try {
    const baseOrigin = new URL(API_BASE, window.location.origin).origin;
    return baseOrigin === window.location.origin ? "include" : "omit";
  } catch {
    return "include";
  }
};

const API_CREDENTIALS = requestCredentials();

export async function apiGet<T>(path: string): Promise<T> {
  const response = await fetch(apiUrl(path), {
    method: "GET",
    headers: {
      Accept: "application/json",
    },
    credentials: API_CREDENTIALS,
  });

  if (!response.ok) {
    throw new Error(`Request failed: ${response.status} ${response.statusText}`);
  }

  return (await response.json()) as T;
}
