// Wraps fetch() with a timeout, so a hung request (e.g. the backend mid-
// restart, or briefly unreachable) fails after a few seconds instead of
// leaving a button stuck "loading" forever with no way to know if it's ever
// coming back. Plain fetch() has no default timeout in the browser -- a
// dropped/ignored request can otherwise sit "in flight" indefinitely.
export async function fetchWithTimeout(url, options = {}, timeoutMs = 15000) {
  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), timeoutMs);
  try {
    return await fetch(url, { ...options, signal: controller.signal });
  } catch (err) {
    if (err.name === 'AbortError') {
      throw new Error('Request timed out. Check your connection and try again.');
    }
    throw err;
  } finally {
    clearTimeout(timeoutId);
  }
}
