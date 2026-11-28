// Private CSV export of the email list.
// Works only when an EXPORT_TOKEN secret is set in Cloudflare; call:
//   /api/subscribers?token=YOUR_TOKEN
export async function onRequestGet({ request, env }) {
  const url = new URL(request.url);
  if (!env.EXPORT_TOKEN || !env.SUBSCRIBERS || url.searchParams.get("token") !== env.EXPORT_TOKEN) {
    return new Response("Not found", { status: 404 });
  }
  const rows = ["email,subscribed_at,source,country"];
  let cursor;
  do {
    const page = await env.SUBSCRIBERS.list({ prefix: "sub:", cursor });
    for (const k of page.keys) {
      const v = JSON.parse((await env.SUBSCRIBERS.get(k.name)) || "{}");
      const esc = (s) => '"' + String(s || "").replace(/"/g, '""') + '"';
      rows.push([esc(v.email), esc(v.subscribed_at), esc(v.source), esc(v.country)].join(","));
    }
    cursor = page.list_complete ? undefined : page.cursor;
  } while (cursor);
  return new Response(rows.join("\n") + "\n", {
    headers: { "content-type": "text/csv", "content-disposition": "attachment; filename=halcott-subscribers.csv", "cache-control": "no-store" },
  });
}
