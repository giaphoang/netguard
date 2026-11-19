export async function get<T>(path: string, signal?: AbortSignal): Promise<T> {
  const response = await fetch("/api/" + path, { cache: "no-store", signal });
  if (!response.ok) throw Error("Controller state is unavailable.");
  return response.json();
}

export async function post(path: string, body: object = {}, accept = "application/json") {
  const session = await fetch("/api/session", { cache: "no-store" });
  if (!session.ok) throw Error("The live NetGuard session is unavailable.");
  const { token } = await session.json();
  const response = await fetch("/api/" + path, {
    method: "POST",
    headers: {
      Accept: accept,
      "Content-Type": "application/json",
      "X-NetGuard-Session": token,
    },
    body: JSON.stringify(body),
  });
  if (!response.ok) {
    const data = await response.json().catch(() => null);
    throw Error(typeof data?.detail === "string" ? data.detail : "Operator request failed.");
  }
  return response;
}
