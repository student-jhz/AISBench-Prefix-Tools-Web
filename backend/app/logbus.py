"""日志总线 - 后台线程写入, WebSocket 增量推送

每个通道(deploy/exec)维护一个带序号的环形缓冲,
客户端断线重连后按 seq 补发历史。
"""

import threading
from collections import deque
from typing import List, Tuple


class LogChannel:
    """单通道日志缓冲"""

    def __init__(self, maxlen: int = 10000):
        self._events = deque(maxlen=maxlen)  # [(seq, text)]
        self._seq = 0
        self._lock = threading.Lock()

    def append(self, text: str) -> int:
        with self._lock:
            self._seq += 1
            self._events.append((self._seq, text))
            return self._seq

    def since(self, after_seq: int = 0) -> List[Tuple[int, str]]:
        """返回 seq 大于 after_seq 的所有事件"""
        with self._lock:
            return [(seq, text) for seq, text in self._events if seq > after_seq]

    def last_seq(self) -> int:
        with self._lock:
            return self._seq

    def clear(self):
        with self._lock:
            self._events.clear()


class LogBus:
    """多通道日志总线"""

    def __init__(self, channels=None):
        self._channels = {name: LogChannel() for name in (channels or ("deploy", "exec"))}

    def append(self, channel: str, text: str) -> int:
        if channel not in self._channels:
            self._channels[channel] = LogChannel()
        return self._channels[channel].append(text)

    def since(self, channel: str, after_seq: int = 0) -> List[Tuple[int, str]]:
        ch = self._channels.get(channel)
        return ch.since(after_seq) if ch else []

    def last_seq(self, channel: str) -> int:
        ch = self._channels.get(channel)
        return ch.last_seq() if ch else 0

    def clear(self, channel: str):
        ch = self._channels.get(channel)
        if ch:
            ch.clear()

    def channel_logger(self, channel: str):
        """返回绑定通道的写入函数"""
        def log(text: str):
            self.append(channel, text)
        return log
