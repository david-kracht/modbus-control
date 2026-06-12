import logging
import socket
from pymodbus.client import AsyncModbusTcpClient

logger = logging.getLogger(__name__)

class ModbusClientWrapper:
    def __init__(self, host: str, port: int = 502, timeout: float = 10.0):
        self.host = host
        self.port = port
        self.timeout = timeout
        self.client = None

    @property
    def connected(self) -> bool:
        return self.client is not None and self.client.connected

    async def connect(self) -> bool:
        if self.client is None:
            self.client = AsyncModbusTcpClient(host=self.host, port=self.port, timeout=self.timeout)
        if not self.client.connected:
            logger.info("Connecting to Modbus TCP at %s:%d", self.host, self.port)
            success = await self.client.connect()
            if success:
                logger.info("Successfully connected to Modbus TCP at %s:%d", self.host, self.port)
                self._apply_keepalive()
            else:
                logger.error("Failed to connect to Modbus TCP at %s:%d", self.host, self.port)
            return success
        return True

    def _apply_keepalive(self) -> None:
        try:
            if self.client and hasattr(self.client, "ctx") and self.client.ctx and getattr(self.client.ctx, "transport", None):
                sock = self.client.ctx.transport.get_extra_info("socket")
                if sock:
                    sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
                    # Linux-specific TCP Keep-Alive parameters
                    if hasattr(socket, "TCP_KEEPIDLE"):
                        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPIDLE, 60)
                    if hasattr(socket, "TCP_KEEPINTVL"):
                        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPINTVL, 10)
                    if hasattr(socket, "TCP_KEEPCNT"):
                        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_KEEPCNT, 6)
                    logger.debug("Applied TCP Keep-Alive options to Modbus client socket")
        except Exception as e:
            logger.warning("Could not set TCP Keep-Alive on socket: %s", e)

    async def close(self) -> None:
        if self.client and self.client.connected:
            self.client.close()
            logger.info("Closed connection to Modbus TCP at %s:%d", self.host, self.port)
