export const API_BASE_URL =
  import.meta.env.VITE_API_BASE_URL || "http://127.0.0.1:8000";

export async function checkBackendHealth() {
  const possibleHealthEndpoints = ["/api/health", "/health", "/"];

  for (const endpoint of possibleHealthEndpoints) {
    try {
      const response = await fetch(`${API_BASE_URL}${endpoint}`);
      if (response.ok) {
        return true;
      }
    } catch {
      // Try the next supported health endpoint.
    }
  }

  return false;
}
