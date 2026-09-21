"""Tiny Python SDK for emitting devtool.test/v1 events from embedded scripts."""
from __future__ import annotations
from .test_protocol import emit
class suite:
    def __init__(self,id,name=None): self.id=id; self.name=name or id
    def __enter__(self): emit({"type":"suite_started","id":self.id,"name":self.name}); return self
    def __exit__(self,exc_type,exc,tb):
        if exc is not None: self.error("uncaught",message=str(exc))
        emit({"type":"suite_finished","id":self.id,"name":self.name}); return False
    def _case(self,id,status,*,name=None,message="",duration_seconds=None): emit({"type":"case","suite":self.id,"id":id,"name":name or id,"status":status,"message":message,"duration_seconds":duration_seconds})
    def pass_(self,id,**kw): self._case(id,"passed",**kw)
    def fail(self,id,**kw): self._case(id,"failed",**kw)
    def error(self,id,**kw): self._case(id,"error",**kw)
    def skip(self,id,**kw): self._case(id,"skipped",**kw)
    def cancel(self,id,**kw): self._case(id,"cancelled",**kw)
