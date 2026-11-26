import asyncio


class FakeColab:
    """In-memory stand-in for the colab CLI. Counts calls; never touches Google."""

    def __init__(self, lines=None, new_delay=0.05, exec_gate: asyncio.Event | None = None):
        self.calls: list[str] = []
        self.new_kwargs: list[dict] = []
        self.live: set[str] = set()
        self.live_details: dict[str, dict[str, str]] = {}
        self.lines = lines if lines is not None else [
            "KIT_STAGE bootstrap start", "KIT_STAGE bootstrap done 1.0",
            "KIT_STAGE download start", 'KIT_PROGRESS download {"done_gb": 1, "total_gb": 2}',
            "KIT_STAGE comfy_boot start", "KIT_STAGE smoke start", 'KIT_READY {"total_s": 5}']
        self.new_delay = new_delay
        self.exec_gate = exec_gate
        self.exec_code = ""

    async def sessions(self):
        self.calls.append("sessions")
        return sorted(self.live)

    async def session_details(self):
        self.calls.append("session_details")
        return {k: dict(v) for k, v in self.live_details.items() if k in self.live}

    async def new(self, name, accelerator, high_mem=False, timeout=180):
        self.calls.append(f"new:{name}:{accelerator}")
        self.new_kwargs.append({"name": name, "accelerator": accelerator, "high_mem": high_mem, "timeout": timeout})
        await asyncio.sleep(self.new_delay)
        self.live.add(name)
        self.live_details[name] = {
            "endpoint": f"gpu-{accelerator.lower()}-s-fake",
            "hardware": accelerator,
            "accelerator": accelerator,
        }

    async def upload(self, name, src, dst):
        self.calls.append("upload")

    async def exec_stream(self, name, code, timeout_s):
        self.calls.append("exec")
        self.exec_code = code
        if self.exec_gate:
            await self.exec_gate.wait()
        for line in self.lines:
            await asyncio.sleep(0)
            yield line

    async def stop(self, name):
        self.calls.append("stop")
        self.live.discard(name)

    async def ccu_info(self):
        return None

    async def touch_heartbeat(self, name: str) -> bool:
        self.calls.append("touch_heartbeat")
        return name in self.live

    async def wait_for_kernel(self, name: str, attempts: int = 5) -> bool:
        self.calls.append("wait_for_kernel")
        return True
