"""Nightly cleanup endpoint (Flask).

An ordinary HTTP endpoint that purges expired/stale records. smplkit calls it on
the schedule your agent sets up; it does NOT import or call any smplkit SDK.

It verifies a shared secret header (X-Job-Secret) and returns 401 without it, so
the public URL can't be triggered by anyone else. It is idempotent — safe to run
again any time, including on demand via run_job.
"""
import os

from flask import Flask, jsonify, request

app = Flask(__name__)


@app.post("/cleanup")
def cleanup():
    expected = os.environ.get("JOB_SECRET")
    if not expected or request.headers.get("X-Job-Secret") != expected:
        return jsonify(error="unauthorized"), 401

    # --- your real work goes here (idempotent: safe to re-run) ---
    deleted = purge_expired_records()
    # -------------------------------------------------------------

    # Returned to smplkit as the run's captured result.
    return jsonify(ok=True, deleted=deleted)


def purge_expired_records() -> int:
    """Delete expired/stale rows; return how many were removed.

    Stubbed to 0 so the example runs without a database. Replace with your real
    query, e.g.::

        DELETE FROM sessions WHERE expires_at < now();

    and return the affected row count.
    """
    return 0


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000)
