#!/usr/bin/env python3
"""
agent.py — Airport Investment Intelligence Agent: the conversation loop.

The model reads the question, decides which signal to ACTIVATE (tool-calling), the
signal runs against BTS r495-tyji, and the model writes the answer.

This file owns ONLY the loop and the memory. The other pieces live next door:
    tools.py    the five tools + their schemas  (what the agent can DO)
    prompts.py  the system prompt              (how the agent BEHAVES)

SETUP   pip install openai
        export OPENAI_API_KEY=...          # and optionally OPENAI_MODEL
RUN     python3 agent.py "Compare LA and Santa Ana congestion"
        python3 agent.py                    # interactive
"""
import json
import os
import sys

from openai import OpenAI

from prompts import SYSTEM
from tools import TOOL_SCHEMAS, TOOLS

MODEL = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")

# ---------- MEMORY: a list of dicts, resent in full on every call ----------
MAX_MESSAGES = 40        # kept after the system prompt; ~10 tool-using turns
MAX_ROUNDS = 5           # tool-calling rounds per question

def call_tool(call):
    """Run one tool the model asked for; return its result as a JSON string.

    The guard is deliberate: a bad IATA code or a BTS outage becomes an
    {"error": ...} the model can read and recover from, instead of killing
    the conversation and losing all history.
    """
    try:
        result = TOOLS[call.function.name](**json.loads(call.function.arguments))
    except Exception as e:
        result = {"error": f"{type(e).__name__}: {e}"}
    return json.dumps(result)

class Conversation:
    """Holds the conversation as self.messages — a growing list of dicts.

    The API is stateless: it remembers nothing between calls. What makes this a
    conversation is that we resend the ENTIRE list every time, so the model can
    re-read earlier turns and resolve follow-ups like "and Boston?".
    """

    def __init__(self, model=None):
        self.client = OpenAI()
        self.model = model or MODEL
        self.messages = [{"role": "system", "content": SYSTEM}]

    def say(self, role, content, **extra):
        """Append one message to memory."""
        self.messages.append({"role": role, "content": content, **extra})

    def ask(self, question):
        self.say("user", question)
        for _ in range(MAX_ROUNDS):
            msg = self._next_message()
            if not msg.tool_calls:
                self.trim()                  # only safe once the turn has settled
                return msg.content
            for call in msg.tool_calls:
                self.say("tool", call_tool(call), tool_call_id=call.id)
        return "Stopped after too many tool rounds."

    def _next_message(self):
        """One model call. The reply is normalised to a plain dict before it
        lands in memory, so self.messages stays uniformly JSON-serialisable
        (exclude_none drops the SDK's refusal/audio/function_call nulls)."""
        resp = self.client.chat.completions.create(
            model=self.model, messages=self.messages, tools=TOOL_SCHEMAS)
        msg = resp.choices[0].message
        self.messages.append(msg.model_dump(exclude_none=True))
        return msg

    def trim(self):
        """Drop oldest messages, keeping the system prompt.

        A 'tool' message is only valid right after the assistant message holding
        its tool_call_id, so never leave one at the front — that's a 400 from the
        API. Walk forward past any orphaned tool results.
        """
        system, rest = self.messages[0], self.messages[1:]
        if len(rest) <= MAX_MESSAGES:
            return
        kept = rest[-MAX_MESSAGES:]
        while kept and kept[0]["role"] == "tool":
            kept.pop(0)
        self.messages = [system] + kept

    def save(self, path):
        """Write the whole conversation to disk — possible because every
        message is a plain dict. Load it back with json.load into .messages."""
        with open(path, "w") as fh:
            json.dump(self.messages, fh, indent=1)

def run(question):
    """One-shot ask — unchanged behaviour for scripted use."""
    return Conversation().ask(question)

QUIT = {"exit", "quit"}

def prompt():
    """Read one line. Returns None on Ctrl-C / Ctrl-D / exit — the guard is
    what turns those into a clean goodbye instead of a traceback."""
    try:
        line = input("you > ").strip()
    except (EOFError, KeyboardInterrupt):
        print()
        return None
    return None if line.lower() in QUIT else line

def repl():
    convo = Conversation()
    print(f"Airport agent ({convo.model}) — follow-ups keep context.")
    print("  reset = clear memory   save = write history.json   exit = quit\n")
    while (q := prompt()) is not None:
        if not q:
            continue
        if q.lower() == "reset":
            convo = Conversation(convo.model)
            print("[memory cleared]\n")
        elif q.lower() == "save":
            convo.save("history.json")
            print(f"[saved {len(convo.messages)} messages to history.json]\n")
        else:
            print(f"\n{convo.ask(q)}\n")

if __name__ == "__main__":
    if len(sys.argv) > 1:
        print(run(" ".join(sys.argv[1:])))   # one-shot, as before
    else:
        repl()                               # conversational
