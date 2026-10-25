"""MTProto over WSS (obfuscated2) for Telethon: box egress allows only HTTP(S)."""
import asyncio, websockets
from telethon.network.connection import ConnectionTcpObfuscated

WSS_HOSTS = {1: "pluto", 2: "venus", 3: "aurora", 4: "vesta", 5: "flora"}

class _WSReader:
    def __init__(self, ws): self.ws, self.buf = ws, bytearray()
    async def readexactly(self, n):
        while len(self.buf) < n:
            m = await self.ws.recv()
            self.buf += m if isinstance(m, (bytes, bytearray)) else m.encode()
        out = bytes(self.buf[:n]); del self.buf[:n]; return out

class _WSWriter:
    def __init__(self, ws): self.ws, self.buf = ws, bytearray()
    def write(self, data): self.buf += data
    async def drain(self):
        if self.buf:
            d = bytes(self.buf); self.buf.clear(); await self.ws.send(d)
    def close(self): self._t = asyncio.ensure_future(self.ws.close())
    async def wait_closed(self): await self.ws.wait_closed()

class ConnectionWSSObfuscated(ConnectionTcpObfuscated):
    """DC chosen by dc_id (works with migration); ip/port ignored."""
    async def _connect(self, timeout=None, ssl=None):
        host = WSS_HOSTS.get(abs(self._dc_id) or 2, "venus")
        ws = await asyncio.wait_for(websockets.connect(
            f"wss://{host}.web.telegram.org/apiws", subprotocols=["binary"],
            max_size=None, ping_interval=None, compression=None), timeout)
        self._reader, self._writer = _WSReader(ws), _WSWriter(ws)
        self._codec = self.packet_codec(self)
        self._init_conn()
        await self._writer.drain()
