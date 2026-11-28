// Email signup endpoint (Cloudflare Pages Function).
// Stores each address in the Workers KV namespace bound as SUBSCRIBERS.
// No third-party email service is involved.

const EMAIL_RE = /^[^\s@]{1,64}@[^\s@]{1,255}\.[^\s@]{2,24}$/;

function reply(request, status, body) {
  const wantsJson = (request.headers.get("accept") || "").includes("application/json");
  if (wantsJson) {
    return new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
  }
  // Plain form post (no JavaScript): send people to a friendly page.
  const to = body.ok ? "/thanks/" : "/thanks/?error=1";
  return Response.redirect(new URL(to, request.url).toString(), 303);
}

export async function onRequestPost({ request, env }) {
  if (!env.SUBSCRIBERS) {
    return reply(request, 503, { ok: false, error: "Signups are temporarily unavailable." });
  }
  let form;
  try {
    form = await request.formData();
  } catch {
    return reply(request, 400, { ok: false, error: "Something went wrong. Please try again." });
  }
  // Honeypot: real people never fill this hidden field.
  if ((form.get("website") || "").toString().trim() !== "") {
    return reply(request, 200, { ok: true });
  }
  const email = (form.get("email") || "").toString().trim().toLowerCase();
  if (!EMAIL_RE.test(email)) {
    return reply(request, 400, { ok: false, error: "Please enter a valid email address." });
  }
  const source = (form.get("source") || "").toString().slice(0, 80);
  const key = "sub:" + email;
  const existing = await env.SUBSCRIBERS.get(key);
  if (!existing) {
    await env.SUBSCRIBERS.put(key, JSON.stringify({
      email,
      subscribed_at: new Date().toISOString(),
      source,
      country: (request.cf && request.cf.country) || "",
    }));
  }
  return reply(request, 200, { ok: true });
}

export async function onRequestGet() {
  return new Response("Method not allowed", { status: 405 });
}
