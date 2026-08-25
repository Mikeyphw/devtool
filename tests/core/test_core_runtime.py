from devtool.core import CommandResult
from devtool.core.events import EventBus, BuildStarted


def test_command_result_serializes():
    result = CommandResult.ok(duration=1.2)
    assert result.to_dict()["success"] is True


def test_event_bus_dispatches():
    bus = EventBus()
    received = []
    bus.subscribe(BuildStarted, received.append)
    bus.publish(BuildStarted(project="demo"))
    assert received[0].project == "demo"
