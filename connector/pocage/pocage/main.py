from __future__ import annotations

import asyncio
import contextlib
import logging
import signal
import sys

from .config import PairSettings, RunSettings, parse_args
from .executor import PocageExecutor
from .pairing import pair_daemon


async def _run_daemon(settings: RunSettings) -> None:
    executor = PocageExecutor(settings)

    loop = asyncio.get_running_loop()
    stop_event = asyncio.Event()

    def handle_stop() -> None:
        stop_event.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, handle_stop)

    worker = asyncio.create_task(executor.run_forever())
    stop_waiter = asyncio.create_task(stop_event.wait())
    done, pending = await asyncio.wait({worker, stop_waiter}, return_when=asyncio.FIRST_COMPLETED)

    for task in pending:
        task.cancel()

    if stop_waiter in done:
        await executor.stop()
        worker.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await worker
        return

    with contextlib.suppress(asyncio.CancelledError, Exception):
        await stop_waiter
    try:
        await worker
    finally:
        await executor.stop()


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    settings = parse_args()
    if isinstance(settings, PairSettings):
        try:
            state = pair_daemon(settings)
        except RuntimeError as exc:
            print(f"pocage pair failed: {exc}", file=sys.stderr, flush=True)
            raise SystemExit(1) from None
        print(
            f"paired {state.agent} daemon: machine_id={state.machine_id} agent_instance_id={state.agent_instance_id}",
            flush=True,
        )
        return
    asyncio.run(_run_daemon(settings))


if __name__ == "__main__":
    main()
