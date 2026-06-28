// Vercel Function — route: POST /api/digest
//
// This is just an ordinary serverless HTTP endpoint. smplkit calls it on the
// schedule your agent sets up; it does NOT import or call any smplkit SDK.
//
// It only does work for callers that present the shared secret header, so the
// public URL can't be triggered by anyone who stumbles onto it. Return 401
// without it — that's the pattern to copy for any endpoint you schedule.

export default async function handler(request: Request): Promise<Response> {
  if (request.method !== "POST") {
    return Response.json({ error: "method not allowed" }, { status: 405 });
  }

  const expected = process.env.JOB_SECRET;
  const provided = request.headers.get("x-job-secret");
  if (!expected || provided !== expected) {
    return Response.json({ error: "unauthorized" }, { status: 401 });
  }

  // --- your real work goes here ---
  const digest = await assembleDigest();
  await sendEmail(digest);
  // --------------------------------

  // Whatever you return is captured by smplkit as the run's result, so make it
  // useful — your agent shows it back to you when it runs the job to prove it.
  return Response.json({
    ok: true,
    subject: digest.subject,
    recipients: digest.recipients,
  });
}

// ---- stubs: replace with your real implementation ----

async function assembleDigest(): Promise<{ subject: string; body: string; recipients: number }> {
  // e.g. query your database for the last 24h of activity and render an email.
  return { subject: "Your daily digest", body: "…the day's summary…", recipients: 0 };
}

async function sendEmail(_digest: { subject: string; body: string }): Promise<void> {
  // e.g. await resend.emails.send({ ... }) — or your SMTP / provider call.
  // Left as a no-op so the example runs with no email provider configured.
}
