"""A stub OpenAI-compatible model server, for the end-to-end suite only.

**Why this exists, and why it is not in the application.** The agent-journey test asserts
that a parked action stays answerable after a reload and that approving it performs the
write. Neither claim depends on what the agent decides *next*. But approving does let the
agent continue, and continuing costs real model calls: measured on this CPU-only host,
`qwen2.5:3b` generates at about **7 tokens/second** — a 256-token completion takes 37.9 s,
512 tokens takes 71 s — so the post-approval continuation took between 8 s and 69 s across
identical runs and the suite failed intermittently on a 60 s budget. The variability is
the model's, not the browser's or the application's.

The alternative was to raise that budget. That does not make the test deterministic; it
just makes it slower and still dependent on how much a 3B model decided to write.

So the model is replaced **at the protocol boundary**. This is a real HTTP server speaking
the OpenAI chat-completions and embeddings shape, which means the application exercises
its actual `httpx` client, its tool-calling normalisation, its tool runner and its
persistence — only the third party on the other end is fake.

**Deliberately not `LLM_PROVIDER=scripted` in `resolve_model`.** A configuration switch
that swaps the forensic agent's reasoning for a canned reply is exactly the kind of thing
that gets left enabled in a deployment, where it would produce confident, wrong findings.
The `ScriptedModel` double already exists in `app.agent.llm` for Python tests, which is the
right place for it; making it reachable from configuration would move it to the wrong one.

Responses are intentionally empty with `finish_reason: "stop"` — the signal the graph
already treats as "the agent has nothing further to do". Run as:

    python backend/scripts/llm_stub.py --port 8080
"""

from __future__ import annotations

import argparse
import json
import logging
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

#: Must match `EMBED_DIM` and `Settings.embed_dim`. A mismatch is rejected on write, so
#: this is not a value that can be casually wrong.
EMBED_DIM = 1024

logger = logging.getLogger("llm_stub")


def _unit_vector(index: int, dim: int) -> list[float]:
    """A deterministic vector, distinct per input.

    Real embeddings carry meaning; this carries determinism, which is all the memory note
    written by an approved action needs to be exercised end to end. It is a constant, not
    a hash, so a caller comparing two notes sees equal vectors and does not have to care.
    """
    return [1.0 if i == index % dim else 0.0 for i in range(dim)]


class Handler(BaseHTTPRequestHandler):
    """Answers the two endpoints the application's LLM adapter calls."""

    protocol_version = "HTTP/1.1"

    def log_message(self, fmt: str, *args: Any) -> None:  # noqa: A002
        # The default handler writes to stderr unbuffered and interleaves; the suite's own
        # output is easier to read with one line per call.
        logger.info("%s %s", self.command, self.path)

    def _send(self, status: int, body: dict[str, Any]) -> None:
        payload = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self) -> None:  # noqa: N802 - required by BaseHTTPRequestHandler
        if self.path.rstrip("/").endswith("/models"):
            self._send(200, {"object": "list", "data": [{"id": "llm-stub"}]})
            return
        self._send(404, {"error": {"message": f"no route for {self.path}"}})

    def do_POST(self) -> None:  # noqa: N802 - required by BaseHTTPRequestHandler
        length = int(self.headers.get("Content-Length") or 0)
        try:
            request = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            self._send(400, {"error": {"message": "body was not JSON"}})
            return

        route = self.path.rstrip("/")
        if route.endswith("/embeddings"):
            self._send(200, self._embeddings(request))
        elif route.endswith("/chat/completions"):
            self._send(200, self._chat(request))
        else:
            self._send(404, {"error": {"message": f"no route for {self.path}"}})

    @staticmethod
    def _embeddings(request: dict[str, Any]) -> dict[str, Any]:
        raw = request.get("input")
        texts = [raw] if isinstance(raw, str) else list(raw or [])
        return {
            "object": "list",
            "model": request.get("model", "llm-stub"),
            "data": [
                {"object": "embedding", "index": i, "embedding": _unit_vector(i, EMBED_DIM)}
                for i, _ in enumerate(texts)
            ],
            "usage": {"prompt_tokens": 0, "total_tokens": 0},
        }

    @staticmethod
    def _chat(request: dict[str, Any]) -> dict[str, Any]:
        """An immediate, empty, successful completion.

        Deliberately *not* a tool call: the approved action has already been applied by the
        time this is reached, so the agent has nothing left to do and saying so is the
        honest reply. Returning a tool call here would start a second write, which is not
        what this stub exists to test.
        """
        return {
            "id": "llm-stub-1",
            "object": "chat.completion",
            "model": request.get("model", "llm-stub"),
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": ""},
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
        }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8080)
    # Loopback by default. A container has to be passed `--host 0.0.0.0` to be reachable
    # from the api service, and that is taken explicitly at the call site rather than being
    # this script's default: a stub that answers on every interface unless asked is a stub
    # that will eventually be running on one in production by accident.
    parser.add_argument("--host", default="127.0.0.1")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    logger.info("llm stub listening on %s:%s", args.host, args.port)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
