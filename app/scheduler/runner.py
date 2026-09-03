import asyncio


class Scheduler:
    def __init__(self, settings, service):
        self.s = settings
        self.service = service

    async def run(self):
        """Monitor trades/context. Leader discovery runs only after the admin explicitly enables V23 leader monitor for that Saudi day."""
        print("[scheduler] started: monitor/news; leader discovery is OFF until admin enables it")
        while True:
            try:
                await self.service.scheduled_tasks()
            except Exception as exc:
                print(f"[scheduler] {exc!r}")
            await asyncio.sleep(max(60, self.s.scan_interval_seconds))
