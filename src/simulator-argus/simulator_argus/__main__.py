from __future__ import annotations

import asyncio
import logging
import signal

from .client import BackendClient
from .config import settings
from .fleet import provision_fleet
from .virtual_truck import VirtualTruck

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


async def run() -> None:
    async with BackendClient(settings.backend_base_url) as client:
        specs = await provision_fleet(client, settings)
        logger.info(
            "Simulating %d virtual truck(s) against %s", len(specs), settings.backend_base_url
        )

        trucks = [
            VirtualTruck(
                client,
                spec,
                settings.simulator_status_interval_seconds,
                settings.simulator_medium_blip_probability,
            )
            for spec in specs
        ]
        tasks = [asyncio.create_task(truck.run()) for truck in trucks]

        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, stop.set)

        await stop.wait()
        logger.info("Shutting down virtual trucks...")
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
