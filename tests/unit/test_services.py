import pytest

from research_harness.kernel.errors import ServiceError
from research_harness.kernel.services import ServiceRegistry


def test_register_and_require():
    reg = ServiceRegistry()
    reg.register("svc.a", object(), owner="p1")
    assert reg.has("svc.a")
    assert reg.get("svc.a") is not None
    assert reg.require("svc.a") is not None


def test_duplicate_detection():
    reg = ServiceRegistry()
    reg.register("svc.a", "first", owner="p1")
    with pytest.raises(ServiceError, match="already registered"):
        reg.register("svc.a", "second", owner="p2")


def test_missing_require():
    reg = ServiceRegistry()
    with pytest.raises(ServiceError, match="not found"):
        reg.require("svc.missing")


def test_unregister():
    reg = ServiceRegistry()
    reg.register("svc.a", "val", owner="p1")
    reg.unregister("svc.a", owner="p1")
    assert not reg.has("svc.a")


def test_unregister_wrong_owner():
    reg = ServiceRegistry()
    reg.register("svc.a", "val", owner="p1")
    with pytest.raises(ServiceError, match="owned by"):
        reg.unregister("svc.a", owner="p2")


def test_clear_owner():
    reg = ServiceRegistry()
    reg.register("svc.a", "v1", owner="p1")
    reg.register("svc.b", "v2", owner="p1")
    reg.register("svc.c", "v3", owner="p2")
    reg.clear_owner("p1")
    assert not reg.has("svc.a")
    assert not reg.has("svc.b")
    assert reg.has("svc.c")
