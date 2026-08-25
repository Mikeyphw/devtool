"""Android/Gradle task classification and availability filtering."""

from ._standalone import (
    connected_android_devices,
    default_tasks,
    filter_unavailable_connected_android_tests,
    gradle_tasks_require_node,
    is_connected_android_test_task,
)

__all__ = [
    "connected_android_devices",
    "default_tasks",
    "filter_unavailable_connected_android_tests",
    "gradle_tasks_require_node",
    "is_connected_android_test_task",
]
