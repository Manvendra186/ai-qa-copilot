// Smoke check: the server boots and /health answers (manual, no test framework).
const res = await fetch("http://localhost:4000/health");
if (!res.ok) {
  throw new Error(`expected 200, got ${res.status}`);
}
console.log("smoke ok:", res.status);
