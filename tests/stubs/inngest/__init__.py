from typing import Any, Callable


class Context:
    def __init__(self, event: Any = None, **kwargs: Any):
        self.event = event
        for key, value in kwargs.items():
            setattr(self, key, value)


class Event:
    def __init__(self, name: str, data: dict[str, Any] | None = None):
        self.name = name
        self.data = data or {}


class PydanticSerializer:
    pass


class TriggerEvent:
    def __init__(self, event: str):
        self.event = event


class RateLimit:
    def __init__(self, limit: int, period: Any, key: str = ""):
        self.limit = limit
        self.period = period
        self.key = key


class Throttle:
    def __init__(self, limit: int, period: Any):
        self.limit = limit
        self.period = period


class Inngest:
    def __init__(
        self,
        app_id: str,
        logger: Any = None,
        is_production: bool = False,
        serializer: Any = None,
    ):
        self.app_id = app_id
        self.logger = logger
        self.is_production = is_production
        self.serializer = serializer

    def create_function(
        self,
        fn_id: str,
        trigger: TriggerEvent,
        retries: int = 0,
        throttle: Throttle | None = None,
        rate_limit: RateLimit | None = None,
    ):
        def decorator(func: Callable):
            return func

        return decorator

    async def send(self, event: Event) -> list[str]:
        return ["test-event-id"]
