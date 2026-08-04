#!/usr/bin/env python3
"""
web.py — HTTP layer for the Airport Investment Intelligence Agent.

The terminal REPL in agent.py serves one person. This serves many, and the design
choice that makes that safe is STATELESSNESS: the browser keeps its own history
and replays it each turn, so no visitor can ever see another's conversation and
any worker can answer any request.

    GET  /            the page: what we measure, then a chat
    GET  /health      liveness probe for the host
    POST /api/ask     {"question": "...", "history": [...]}
                   -> {"answer": "...", "history": [...], "trace": [...],
                       "debug": bool}

`trace` is what the tools actually returned this turn. The page charts it, so
every visual is the measurement itself rather than a re-reading of the prose.

Deliberately a long-running server, not serverless: bts.py caches BTS responses
in-process, so a warm server answers follow-ups in ~1ms where a cold serverless
function would re-fetch every time.

SETUP  pip install -r requirements.txt
       export OPENAI_API_KEY=...
RUN    ./run.sh          local (frees the port, opens the browser, reloads on edit)
       python3 web.py    the server alone

One threaded process, not gunicorn's forked workers: forking after the OpenAI
client loads segfaults on macOS, and threads are plenty for a demo since every
request is I/O-bound on BTS anyway.
"""
import json
import os
import re
import sys

from flask import Flask, jsonify, render_template, request

# The KPI modules import each other flatly (`from bts import ...`), so their
# directory has to be importable no matter where the server is launched from.
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                "data_extractor_by_kpi"))

from agent import Conversation          # noqa: E402  (must follow sys.path)

app = Flask(__name__)

MAX_HISTORY = 40         # messages accepted from a client, after the system prompt
MAX_QUESTION = 500       # characters — nothing useful is longer, and it caps abuse
CLIENT_ROLES = {"user", "assistant", "tool"}     # never "system" — see clean()


DEBUG_RE = re.compile(r"\s*\(\s*debug\s*\)\s*$", re.I)


def strip_debug(question):
    """'How congested is SFO? (debug)' -> ('How congested is SFO?', True).

    The marker is removed before the model sees it, so asking for a trace cannot
    change the answer you were going to get.
    """
    stripped = DEBUG_RE.sub("", question)
    return (stripped.strip() or question), stripped != question


def tool_trace(new_messages):
    """What actually ran this turn, read from the recorded messages.

    Deliberately NOT asked of the model: it can misremember or invent a tool it
    never called. These are the calls the loop really dispatched, paired with what
    each returned.
    """
    results = {m["tool_call_id"]: m.get("content", "")
               for m in new_messages if m.get("role") == "tool"}
    trace = []
    for m in new_messages:
        for c in (m.get("tool_calls") or []):
            raw = results.get(c["id"], "")
            try:
                out = json.loads(raw)
            except ValueError:
                out = raw
            trace.append({"tool": c["function"]["name"],
                          "arguments": c["function"].get("arguments", ""),
                          "found": out.get("found") if isinstance(out, dict) else None,
                          "result": out})
    return trace


def clean(history):
    """Keep only the roles a real conversation produces.

    The client can send anything, and a smuggled {"role":"system"} message would
    sit beside our own and override the agent's rules — tested, it survives
    trim(). Dropping the role entirely is the fix.
    """
    return [m for m in history
            if isinstance(m, dict) and m.get("role") in CLIENT_ROLES]


@app.post("/api/ask")
def ask():
    """Stateless: the browser sends its own history back, and we return the new
    one. Nothing is shared between visitors and there is no session store, so any
    process can answer any request — which is what makes this safe to host, and
    what keeps a restart from wiping someone's conversation mid-demo.

    The history is untrusted input, so Conversation rebuilds its own system
    prompt and only the message list is adopted.
    """
    data = request.get_json(silent=True) or {}
    question = (data.get("question") or "").strip()[:MAX_QUESTION]
    if not question:
        return jsonify(error="Ask a question first."), 400

    # "…question (debug)" asks what actually ran. Strip the marker before the model
    # sees it, so the question itself is unchanged.
    question, debug = strip_debug(question)

    convo = Conversation()
    history = data.get("history")
    if isinstance(history, list):
        convo.messages += clean(history)[-MAX_HISTORY:]
        convo.trim()                     # never leave a tool message orphaned

    # Mark the messages that already exist BY IDENTITY, not by index. ask() calls
    # trim() before returning, which drops messages off the front — so a saved
    # length silently points at the wrong place once a conversation gets long, and
    # the trace comes back empty exactly when the demo has been running a while.
    seen = {id(m) for m in convo.messages}
    try:
        answer = convo.ask(question)
    except Exception as e:
        # The model or BTS failed. Report it instead of a 500 page, so the chat
        # stays usable and the user can retry.
        return jsonify(error=f"{type(e).__name__}: {e}"), 502

    # The trace goes back on EVERY turn, because the charts are drawn from it —
    # they render the tool's own numbers, never the model's prose, so a chart can
    # never disagree with the text. `debug` only controls whether the raw JSON
    # panel is also shown.
    fresh = [m for m in convo.messages if id(m) not in seen]
    payload = {"answer": answer, "history": convo.messages[1:],
               "trace": tool_trace(fresh), "debug": debug}
    # Hand back everything except the system prompt — the client stores it and
    # replays it next turn.
    return jsonify(**payload)


@app.get("/health")
def health():
    """Hosts poll this to decide whether the container is alive."""
    return jsonify(ok=True)


@app.get("/")
def home():
    """The page itself is templates/index.html — plain HTML/CSS/JS, no build step.
    Kept out of this file so it can be edited as HTML rather than as a Python
    string."""
    return render_template("index.html")



if __name__ == "__main__":
    if not os.environ.get("OPENAI_API_KEY"):
        sys.exit("Set OPENAI_API_KEY first:  export OPENAI_API_KEY=...")
    # threaded=True so one slow BTS query does not block other visitors. Threads
    # (not gunicorn's forked workers) are also the only thing that runs reliably
    # on macOS — forking after the OpenAI client loads segfaults there.
    #
    # 5001, not 5000: macOS ControlCenter (AirPlay Receiver) already listens on
    # 5000. Render sets $PORT itself, so this default only affects local runs.
    #
    # RELOAD=1 restarts on any .py change. prompts.py is read once at import, so
    # without this a prompt edit silently does nothing until you restart by hand —
    # which looks exactly like the model ignoring the new instruction.
    reload_on_edit = os.environ.get("RELOAD") == "1"
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5001)),
            threaded=True, debug=False, use_reloader=reload_on_edit)
