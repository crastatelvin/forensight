"""Draining: what a deployment does between being told to stop and actually stopping.

A container receives SIGTERM and has a grace period, and the order things happen in
decides whether a shutdown is invisible or is a report that fails to generate.

**Readiness has to flip first.** `/readyz` is what a load balancer polls to decide
where to send traffic. If it keeps answering 200 until the process dies, the balancer
keeps handing it new requests for the whole grace period, and every one of them is
cut off mid-flight. So the first thing shutdown does is stop claiming to be ready.

**Then new work is refused, while in-flight work continues.** The two are different
questions and they are separated on purpose:

- A *new* long operation — an agent run, an analysis submission — is refused with a
  message telling the caller to retry. Refusing costs the caller one retry; starting
  work that will be killed halfway costs the caller the work and leaves a half-written
  row behind.
- Work already accepted keeps running. A report being generated at the moment of the
  signal should finish; that is often the artifact someone is waiting for, and
  killing it is the one outcome a graceful shutdown exists to avoid.

**This is a flag, not a mechanism.** There is no socket draining, no connection
tracking, no request counter. The platform sets the flag, waits its grace period, and
uvicorn closes the sockets. Anything more would be machinery for a deployment shape
this project does not have — single-node compose, one worker pool — and the machinery
would need testing to be believed.

The flag is process-global for the same reason. Two API processes under a balancer
each get SIGTERM separately, and each has to answer for itself: a shared flag would
have one process claim to be draining while another still advertises itself as ready,
and the balancer would keep routing to the wrong one.
"""

from __future__ import annotations

import logging
import threading

logger = logging.getLogger("forensight.drain")

#: Set on SIGTERM, before anything else happens.
#:
#: A plain bool behind a lock rather than an asyncio primitive: it is read from
#: request handlers and written from a signal handler, and the write happens once.
#: `threading.Lock` is the only thing in the standard library that is safe to touch
#: from both without awaiting.
_draining = False
_lock = threading.Lock()


def begin_draining(reason: str = "SIGTERM") -> None:
    """Mark this process as draining. Idempotent.

    Called from the signal handler, so it must not raise and must not do work. It
    logs once, at INFO, because "why did this pod go unready" is a question an
    operator asks during an incident and the answer should be in the log already.
    """
    global _draining
    with _lock:
        already = _draining
        _draining = True
    if not already:
        logger.info("draining: %s received, refusing new work", reason)


def is_draining() -> bool:
    """Whether this process has been asked to stop accepting work."""
    with _lock:
        return _draining


def reset() -> None:
    """Clear the flag. Tests only — the process is not coming back in production."""
    global _draining
    with _lock:
        _draining = False


def refusal(what: str) -> str:
    """The message a caller gets while draining.

    Names the retry, because a caller that is told only "unavailable" cannot tell a
    draining pod from a broken one, and will report an outage for a routine deploy.
    """
    return (
        f"this server is shutting down and is not accepting {what}. Nothing was "
        f"started. Retry against another instance, or in a few seconds."
    )


__all__ = ["begin_draining", "is_draining", "refusal", "reset"]
