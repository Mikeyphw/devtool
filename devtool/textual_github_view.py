"""Textual GitHub Actions workspace, reusing canonical read/mutation authorities.

Remote operations are never implicit. Explicit confirmations delegate to the
existing Devtool journaled run-control and source-bound dispatch authorities.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping
import json
import time

from .textual_github_workspace import (
    GithubProjectionError, ActionsSelection, control_github_run, dispatch_github_workflow, display_status,
    load_github_actions, read_github_log_preview, search_actions,
)


def create_github_workspace_widget(repo_root: str | Path, *,
        loader: Any = None, log_reader: Any = None,
        run_controller: Any = None, workflow_dispatcher: Any = None,
        refresh_seconds: float = 45.0):
    """Lazily create a Textual widget; no full-screen launcher hosting."""
    from textual.containers import Horizontal, HorizontalScroll, Vertical
    from textual.css.query import NoMatches
    from textual.screen import ModalScreen
    from textual.widget import Widget
    from textual.widgets import Button, DataTable, Input, Label, RichLog, Static, Tree

    root = Path(repo_root).expanduser().resolve(strict=False)
    load = loader or load_github_actions
    load_logs = log_reader or read_github_log_preview
    control = run_controller or control_github_run
    dispatch = workflow_dispatcher or dispatch_github_workflow

    class RunControlModal(ModalScreen[bool]):
        DEFAULT_CSS = """
        RunControlModal { align: center middle; }
        #github-confirm-box { width: 85%; max-width: 74; height: auto;
                              border: round $warning; background: $surface; padding: 1 2; }
        #github-confirm-title { height: auto; min-height: 3; }
        #github-confirm-buttons { height: 3; min-height: 3; }
        #github-confirm-buttons Button { margin-right: 2; }
        """
        BINDINGS = [("escape", "dismiss_control", "Cancel")]
        def __init__(self, prompt: str) -> None:
            super().__init__()
            self.prompt = prompt
        def compose(self):
            with Vertical(id="github-confirm-box"):
                yield Static(self.prompt, id="github-confirm-title")
                with Horizontal(id="github-confirm-buttons"):
                    yield Button("No",id="github-confirm-no")
                    yield Button("Confirm action",id="github-confirm-yes",variant="warning")
        def on_button_pressed(self,event:Button.Pressed) -> None:
            self.dismiss(event.button.id == "github-confirm-yes")
            event.stop()
        def action_dismiss_control(self) -> None:
            self.dismiss(False)

    class WorkflowDispatchModal(ModalScreen[dict[str,Any] | None]):
        DEFAULT_CSS = """
        WorkflowDispatchModal { align: center middle; }
        #github-dispatch-box { width: 90%; max-width: 85; height: auto;
                               border: round $warning; background: $surface; padding: 1 2; }
        #github-dispatch-box Input { height: 3; margin-top: 1; }
        #github-dispatch-message { height: auto; min-height: 2; }
        #github-dispatch-buttons { height: 3; min-height: 3; }
        #github-dispatch-buttons Button { margin-right: 2; }
        """
        BINDINGS = [("escape", "dismiss_dispatch", "Cancel")]
        def __init__(self, workflow: int, default_ref: str) -> None:
            super().__init__()
            self.workflow = workflow
            self.default_ref = default_ref
        def compose(self):
            with Vertical(id="github-dispatch-box"):
                yield Static(f"Dispatch workflow {self.workflow}? This POST is non-idempotent and may start CI.")
                yield Input(self.default_ref,placeholder="Branch or tag to run",id="github-dispatch-ref")
                yield Input("{}",placeholder='JSON inputs, e.g. {"debug":"true"}',id="github-dispatch-inputs")
                yield Static("Remote ref must match your clean local HEAD; required YAML inputs are validated.",id="github-dispatch-message")
                with Horizontal(id="github-dispatch-buttons"):
                    yield Button("No",id="github-dispatch-no")
                    yield Button("Dispatch once",id="github-dispatch-yes",variant="warning")
        def on_button_pressed(self,event:Button.Pressed) -> None:
            if event.button.id == "github-dispatch-no":
                self.dismiss(None)
            elif event.button.id == "github-dispatch-yes":
                ref=self.query_one("#github-dispatch-ref",Input).value.strip()
                raw=self.query_one("#github-dispatch-inputs",Input).value
                try:
                    inputs=json.loads(raw)
                    if not isinstance(inputs,dict) or not ref:
                        raise ValueError()
                except (ValueError,TypeError):
                    self.query_one("#github-dispatch-message",Static).update("A ref and a JSON object of inputs are required; nothing sent")
                    return
                self.dismiss({"ref":ref,"inputs":inputs})
            event.stop()
        def action_dismiss_dispatch(self) -> None:
            self.dismiss(None)

    class GithubWorkspace(Widget):
        can_focus = True
        DEFAULT_CSS = """
        GithubWorkspace { layout: vertical; height: 1fr; min-height: 0; padding: 0 1; overflow-y: auto; }
        #github-caption { height: 1; text-style: bold; }
        #github-controls { height: 3; min-height: 3; }
        #github-search { width: 1fr; }
        #github-controls Button { width: auto; min-width: 8; margin-left: 1; }
        #github-control-actions { height: 3; min-height: 3; overflow-x: auto; }
        #github-control-actions Button { width: auto; min-width: 10; margin-left: 1; }
        #github-body { height: 1fr; min-height: 0; overflow-y: auto; }
        #github-lists { width: 3fr; height: 1fr; min-height: 0; }
        #github-detail { width: 2fr; height: 1fr; min-height: 0; border-left: solid $panel; overflow-y: auto; }
        #github-workflows { height: 2fr; min-height: 3; }
        #github-runs { height: 3fr; min-height: 4; }
        .github-group-title { height: 1; color: $text-muted; }
        .github-pager { height: 3; min-height: 3; }
        .github-pager Button { width: auto; min-width: 8; margin-right: 1; }
        .github-page-number { width: 1fr; content-align: right middle; }
        #github-selected { height: auto; max-height: 5; padding: 0 1; }
        #github-jobs { height: 2fr; min-height: 3; }
        #github-log-controls { height: 3; min-height: 3; }
        #github-log-filter { width: 1fr; }
        #github-load-logs { width: auto; min-width: 10; margin-left: 1; }
        #github-log-lines { height: 2fr; min-height: 3; border: round $panel; }
        #github-status { height: auto; min-height: 1; max-height: 3; color: $text-muted; }
        .compact #github-body { layout: vertical; }
        .compact #github-lists, .compact #github-detail { width: 1fr; border-left: none; }
        .compact #github-lists { height: 2fr; }
        .compact #github-detail { height: 3fr; border-top: solid $panel; }
        .short #github-controls { height: 2; min-height: 2; }
        .short #github-workflows { height: 3; min-height: 2; }
        .short #github-runs { height: 4; min-height: 2; }
        .tiny #github-log-lines { min-height: 2; }
        """

        def __init__(self, **kwargs: Any) -> None:
            super().__init__(**kwargs)
            self._live = False
            self._snapshot: dict[str, Any] = {}
            self._error = ""
            self._stale = False
            self._loading = False
            self._generation = 0
            self._last_fetch = 0.0
            self._selected_run = 0
            self._workflow = 0
            self._workflow_page = 0
            self._run_page = 0
            self._query = ""
            self._log_filter = ""
            self._log_preview: dict[str, Any] | None = None
            self._log_loading = False
            self._log_generation = 0
            self._remote_loading = False
            self._remote_generation = 0
            self._remote_notice = ""
            self._draw_tables = False
            self._fingerprints: dict[str, tuple[Any, ...]] = {}
            self._tree_fingerprint: tuple[Any, ...] | None = None
            self._shown_logs: list[str] = []
            self._log_fingerprint: tuple[Any, ...] | None = None
            self._timer: Any = None

        def compose(self):
            yield Label("GITHUB ACTIONS   ·   inspect workflows, runs, attempts, jobs and logs", id="github-caption")
            with HorizontalScroll(id="github-controls"):
                yield Input(placeholder="Find a workflow, branch, SHA or run…", id="github-search")
                yield Button("Refresh", id="github-refresh", variant="primary")
                yield Button("Dispatch…", id="github-dispatch")
            with Horizontal(id="github-body"):
                with Vertical(id="github-lists"):
                    yield Static("WORKFLOWS  ·  select a filter", classes="github-group-title")
                    yield DataTable(id="github-workflows")
                    with Horizontal(classes="github-pager"):
                        yield Button("< Previous", id="github-workflows-prev")
                        yield Button("Next >", id="github-workflows-next")
                        yield Static("", id="github-workflows-count", classes="github-page-number")
                    yield Static("RECENT RUNS  ·  up to 100 from GitHub", classes="github-group-title")
                    yield DataTable(id="github-runs")
                    with Horizontal(classes="github-pager"):
                        yield Button("< Previous", id="github-runs-prev")
                        yield Button("Next >", id="github-runs-next")
                        yield Static("", id="github-runs-count", classes="github-page-number")
                with Vertical(id="github-detail"):
                    yield Static("Select a run to inspect its current attempt", id="github-selected")
                    yield Tree("EXACT ATTEMPT JOBS", id="github-jobs")
                    with HorizontalScroll(id="github-log-controls"):
                        yield Input(placeholder="Filter fetched log lines…", id="github-log-filter")
                        yield Button("Fetch logs", id="github-load-logs")
                    with HorizontalScroll(id="github-control-actions"):
                        yield Button("Cancel run…", id="github-cancel")
                        yield Button("Rerun…", id="github-rerun")
                    yield RichLog(id="github-log-lines", wrap=False, highlight=False,
                                  markup=False, max_lines=400, auto_scroll=False)
            yield Static("GitHub GET: read-only observation · dispatch/cancel/rerun require confirmation · not CI qualification", id="github-status")

        def on_mount(self) -> None:
            self._live = True
            for selector, fields in (
                ("#github-workflows", ("Workflow", "State", "ID")),
                ("#github-runs", ("Run", "Status", "Branch", "SHA")),
            ):
                table = self.query_one(selector, DataTable)
                table.cursor_type = "row"
                table.zebra_stripes = True
                table.add_columns(*fields)
            self._timer = self.set_interval(max(10.0, refresh_seconds), self._tick)
            self.call_after_refresh(self.activate)

        def on_unmount(self) -> None:
            self._live = False
            self._generation += 1
            self._log_generation += 1
            self._remote_generation += 1
            if self._timer is not None:
                self._timer.stop()
                self._timer = None

        def activate(self) -> None:
            if self._live and self.is_on_screen and not self._snapshot and not self._loading:
                self.request_refresh()

        def _tick(self) -> None:
            if not self._live or not self.is_on_screen or self._loading:
                return
            cadence = max(30.0, refresh_seconds)
            if time.monotonic() - self._last_fetch >= cadence:
                self.request_refresh()

        def request_refresh(self) -> None:
            if not self._live or self._loading:
                return
            self._loading = True
            self._generation += 1
            generation, selected = self._generation, self._selected_run
            self._status("Refreshing canonical GitHub Actions data…")
            try:
                self.run_worker(lambda: self._background(generation, selected),
                                thread=True, exclusive=True, group="github-workspace-read")
            except Exception as exc:
                self._loading = False
                self._stale = True
                self._error = "Cannot start GitHub fetch: " + type(exc).__name__
                self._render_workspace()

        def _background(self, generation: int, selected: int) -> None:
            try:
                data = load(root, selection=ActionsSelection("", run_id=selected or None, limit=100))
                error = ""
            except Exception as exc:
                data, error = {}, "GitHub read failed (" + type(exc).__name__ + ")"
            try:
                self.app.call_from_thread(self._finish, generation, selected, data, error)
            except (RuntimeError, AttributeError):
                pass

        def _finish(self, generation: int, selected: int, data: Mapping[str, Any], error: str) -> None:
            if not self._live or generation != self._generation:
                return
            self._loading = False
            self._last_fetch = time.monotonic()
            self._error = error
            self._stale = bool(error)
            if not error:
                if data.get("read_only") is not True or data.get("release_qualified") is not False:
                    self._stale = True
                    self._error = "Untrusted Actions projection contract"
                else:
                    self._snapshot = dict(data)
                    verified = data.get("selected_run")
                    if selected and (not isinstance(verified, Mapping) or verified.get("id") != selected):
                        self._snapshot["jobs"] = []
                        self._stale = True
                        self._error = "Selected run detail not bound to current selection"
            self._render_workspace()
            # Selection may change while the previous request is pending. Do
            # not let an earlier run's jobs remain visible under the new run.
            if selected != self._selected_run:
                self.request_refresh()

        def _replace_table(self, selector: str, rows: list[tuple[str, ...]],
                           identities: list[str]) -> None:
            table = self.query_one(selector, DataTable)
            fingerprint = (tuple(rows), tuple(identities))
            if self._fingerprints.get(selector) == fingerprint:
                return
            previous = ""
            try:
                if table.row_count:
                    previous = str(table.coordinate_to_cell_key(table.cursor_coordinate).row_key.value)
            except Exception:
                pass
            cursor = int(table.cursor_row or 0)
            self._draw_tables = True
            try:
                table.clear(columns=False)
                for row, identity in zip(rows, identities):
                    table.add_row(*row, key=identity)
                if rows:
                    chosen = identities.index(previous) if previous in identities else min(cursor, len(rows)-1)
                    table.move_cursor(row=chosen, column=0, animate=False)
            finally:
                self._draw_tables = False
            self._fingerprints[selector] = fingerprint

        def _render_workspace(self) -> None:
            if not self._live:
                return
            try:
                data = self._snapshot
                workflows = search_actions(data.get("workflows") or [], self._query)
                runs = search_actions(data.get("runs") or [], self._query)
                if self._workflow:
                    runs = [item for item in runs if item.get("workflow_id") == self._workflow]
                workflows = [{"id": 0, "name": "All workflows", "state": "", "path": ""}] + workflows
                wf_page_size, run_page_size = 15, 16
                self._workflow_page = min(self._workflow_page, max(0, (len(workflows)-1)//wf_page_size))
                self._run_page = min(self._run_page, max(0, (len(runs)-1)//run_page_size))
                wp, rp = self._workflow_page, self._run_page
                wf_visible = workflows[wp*wf_page_size:(wp+1)*wf_page_size]
                run_visible = runs[rp*run_page_size:(rp+1)*run_page_size]
                self._replace_table("#github-workflows",
                    [(str(x.get("name") or "-"),str(x.get("state") or "-"),str(x["id"])) for x in wf_visible],
                    ["workflow-"+str(x["id"]) for x in wf_visible])
                self._replace_table("#github-runs",
                    [(str(x["id"]),display_status(x),str(x.get("branch") or "-"),str(x.get("sha") or "-")[:12]) for x in run_visible],
                    ["run-"+str(x["id"]) for x in run_visible])
                for prefix, idx, count, width in (("workflows", wp, len(workflows), wf_page_size),
                                                  ("runs", rp, len(runs), run_page_size)):
                    self.query_one("#github-"+prefix+"-prev",Button).disabled = idx == 0
                    self.query_one("#github-"+prefix+"-next",Button).disabled = (idx+1)*width >= count
                    self.query_one("#github-"+prefix+"-count",Static).update(
                        f"Page {idx+1}/{max(1,(count+width-1)//width)} · {count}")
                selected = data.get("selected_run")
                bound = (isinstance(selected,Mapping) and selected.get("id") == self._selected_run
                         and self._selected_run > 0)
                if bound:
                    label = (f"Run {selected['id']} · attempt {selected.get('run_attempt') or '?'} "
                             f"· {display_status(selected)}\n"
                             f"{str(selected.get('sha') or '-')[:40]} · {selected.get('event') or '-'}")
                elif self._selected_run:
                    label = f"Run {self._selected_run} · loading exact attempt…"
                else:
                    label = "Select a run to inspect its current attempt and jobs"
                self.query_one("#github-selected",Static).update(label)
                jobs = data.get("jobs") if bound and data.get("jobs_scope")=="exact-run-attempt" else []
                fingerprint = (self._selected_run, repr(jobs), data.get("jobs_scope"))
                tree = self.query_one("#github-jobs",Tree)
                if self._tree_fingerprint != fingerprint:
                    tree.clear()
                    tree.root.set_label("Exact attempt jobs" if bound else "Jobs · select a run")
                    for job in jobs if isinstance(jobs,list) else []:
                        node = tree.root.add(str(job.get("name") or job.get("id"))+" ["+display_status(job)+"]")
                        for step in job.get("steps") or []:
                            node.add_leaf(str(step.get("name") or "-")+" ["+display_status(step)+"]")
                        node.expand()
                    tree.root.expand()
                    self._tree_fingerprint = fingerprint
                can_control = bool(bound and selected.get("run_attempt") and selected.get("sha")) and not self._remote_loading
                self.query_one("#github-load-logs",Button).disabled = not can_control or self._log_loading
                self.query_one("#github-cancel",Button).disabled = not can_control
                self.query_one("#github-rerun",Button).disabled = not can_control
                self.query_one("#github-dispatch",Button).disabled = not self._workflow or self._remote_loading
                self._render_logs()
                flags=[]
                if data.get("runs_limit_reached"):
                    flags.append("run list capped at 100; not exhaustive")
                if data.get("workflows_limit_reached"):
                    flags.append("workflow list potentially truncated")
                if data.get("jobs_limit_reached"):
                    flags.append("job list potentially truncated")
                if self._stale:
                    flags.append("STALE last-known data")
                suffix=(" · " + "; ".join(flags)) if flags else ""
                self._status((self._remote_notice + " · " if self._remote_notice else "")+
                    (self._error + " · " if self._error else "")+
                    f"{data.get('repository') or 'GitHub'} · {len(runs)} visible runs · NOT CI evidence · remote state unqualified"+suffix)
            except NoMatches:
                self._live = False

        def _status(self, msg: str) -> None:
            try:
                self.query_one("#github-status",Static).update(msg)
            except NoMatches:
                pass

        def _render_logs(self) -> None:
            log = self.query_one("#github-log-lines",RichLog)
            preview = self._log_preview
            selected = self._snapshot.get("selected_run")
            bound = (isinstance(selected,Mapping) and selected.get("id") == self._selected_run)
            valid = (bound and preview and preview.get("run_id") == self._selected_run
                     and preview.get("run_attempt") == selected.get("run_attempt")
                     and preview.get("sha") == str(selected.get("sha") or "").lower())
            lines = preview.get("lines",[]) if valid else []
            term = self._log_filter.casefold().strip()
            visible = [line for line in lines if term in line.casefold()] if term else lines
            fingerprint = (self._selected_run, selected.get("run_attempt") if bound else None,
                           tuple(visible))
            if fingerprint == self._log_fingerprint:
                return
            log.clear()
            for line in visible:
                log.write(line)
            self._shown_logs = list(visible)
            self._log_fingerprint = fingerprint

        def _request_logs(self) -> None:
            if self._log_loading or not self._live:
                return
            selected = self._snapshot.get("selected_run")
            if not isinstance(selected,Mapping) or selected.get("id") != self._selected_run:
                self._status("Select a verified run before fetching logs")
                return
            attempt, sha = selected.get("run_attempt"), str(selected.get("sha") or "")
            if type(attempt) is not int or attempt <= 0 or len(sha) != 40:
                self._status("Run lacks a verified attempt or HEAD; logs unavailable")
                return
            self._log_loading = True
            self._log_generation += 1
            generation, identity = self._log_generation, self._selected_run
            self._status(f"Fetching logs for run {identity}, attempt {attempt} via canonical safe downloader…")
            self.query_one("#github-load-logs",Button).disabled = True
            try:
                self.run_worker(lambda: self._logs_background(generation, identity, attempt, sha),
                                thread=True, exclusive=True, group="github-workspace-logs")
            except Exception as exc:
                self._log_loading = False
                self._status("Log worker unavailable ("+type(exc).__name__+")")

        def _logs_background(self, generation: int, identity: int, attempt: int, sha: str) -> None:
            try:
                preview = load_logs(root,run_id=identity,attempt=attempt,head_sha=sha)
                error = ""
            except Exception as exc:
                preview, error = {}, "Log preview unavailable ("+type(exc).__name__+")"
            try:
                self.app.call_from_thread(self._finish_logs,generation,preview,error)
            except (RuntimeError,AttributeError):
                pass

        def _finish_logs(self,generation:int,preview:Mapping[str,Any],error:str) -> None:
            if not self._live or generation != self._log_generation:
                return
            self._log_loading = False
            selected = self._snapshot.get("selected_run")
            if (not isinstance(selected,Mapping) or preview.get("run_id") != self._selected_run
                    or preview.get("run_attempt") != selected.get("run_attempt")
                    or preview.get("sha") != str(selected.get("sha") or "").lower()):
                if error:
                    self._status(error)
                else:
                    self._status("Run changed while logs loaded; refresh and retry")
            else:
                self._log_preview = dict(preview)
                self._log_fingerprint = None
                self._render_logs()
                self._status(f"Log preview · {len(preview.get('lines',[]))} bounded lines"
                             + (" · TRUNCATED" if preview.get("truncated") else "")
                             + " · not qualification evidence")
            self.query_one("#github-load-logs",Button).disabled = False

        def _confirm_run_control(self, action: str) -> None:
            if self._remote_loading:
                return
            selected = self._snapshot.get("selected_run")
            if not isinstance(selected,Mapping) or selected.get("id") != self._selected_run:
                self._status("Select a run with verified current attempt before remote control")
                return
            identity, attempt = selected.get("id"), selected.get("run_attempt")
            sha = str(selected.get("sha") or "")
            if type(identity) is not int or type(attempt) is not int or len(sha) != 40:
                self._status("Run HEAD or attempt unavailable; no remote control sent")
                return
            prompt = f"{action.upper()} run {identity} attempt {attempt} at HEAD {sha[:12]}?\nThis is a remote GitHub mutation; acceptance is NOT completion."
            self.app.push_screen(RunControlModal(prompt),
                lambda answer: self._send_remote("run",identity,attempt,sha,action) if answer else None)

        def _confirm_workflow_dispatch(self) -> None:
            if self._remote_loading or not self._workflow:
                self._status("Select an exact workflow before dispatch")
                return
            workflow=self._workflow
            selected=self._snapshot.get("selected_run")
            default_ref=str(selected.get("branch") or "") if isinstance(selected,Mapping) else ""
            # Reference is entered/confirmed explicitly, never guessed from
            # an unrelated workflow's latest run.
            self.app.push_screen(WorkflowDispatchModal(workflow,default_ref),
                lambda answer: self._send_remote("dispatch",workflow,answer["ref"],answer["inputs"])
                if isinstance(answer,dict) else None)

        def _send_remote(self, kind: str, *args: Any) -> None:
            if not self._live or self._remote_loading:
                return
            self._remote_loading = True
            self._remote_generation += 1
            generation=self._remote_generation
            self._status("Submitting explicitly confirmed remote request via canonical journal…")
            try:
                self.run_worker(lambda: self._remote_background(generation,kind,args),
                                group="github-remote-control",exclusive=True,thread=True)
            except Exception as exc:
                self._remote_loading = False
                self._status("Remote request worker not started ("+type(exc).__name__+")")

        def _remote_background(self,generation:int,kind:str,args:tuple[Any,...]) -> None:
            try:
                if kind=="run":
                    identity,attempt,sha,action=args
                    result=control(root,run_id=identity,attempt=attempt,head_sha=sha,action=action)
                else:
                    workflow,ref,inputs=args
                    result=dispatch(root,workflow_id=workflow,ref=ref,inputs=inputs)
                error=""
            except GithubProjectionError as exc:
                # The adapter emits bounded public errors, never signed URLs
                # or raw credentials. Other exceptions disclose only type.
                result,error={},str(exc)[:180]
            except Exception as exc:
                result,error={},"Unexpected remote error ("+type(exc).__name__+")"
            try:
                self.app.call_from_thread(self._complete_remote,generation,kind,result,error)
            except (RuntimeError,AttributeError):
                pass

        def _complete_remote(self,generation:int,kind:str,result:Mapping[str,Any],error:str) -> None:
            if not self._live or generation!=self._remote_generation:
                return
            self._remote_loading = False
            if error:
                self._remote_notice = "REMOTE REFUSED / OUTCOME UNCERTAIN: " + error + " · inspect journal before retry"
            else:
                identity=result.get("control_id") or result.get("request_id")
                if not isinstance(identity,str) or len(identity)!=32 or any(c not in "0123456789abcdef" for c in identity.lower()):
                    self._remote_notice = "Unverified remote receipt; inspect canonical journal; do not retry blindly"
                elif result.get("resume_existing"):
                    self._remote_notice = "Existing dispatch journal reused " + identity + "; NO second POST"
                else:
                    self._remote_notice = "Remote request submitted " + identity + " · pending GitHub confirmation; NOT completion"
            self._render_workspace()
            if not error:
                self.request_refresh()

        def on_button_pressed(self,event:Button.Pressed) -> None:
            ident = event.button.id or ""
            if not ident.startswith("github-"):
                return
            if ident == "github-refresh":
                self.request_refresh()
            elif ident == "github-cancel":
                self._confirm_run_control("cancel")
            elif ident == "github-rerun":
                self._confirm_run_control("rerun")
            elif ident == "github-dispatch":
                self._confirm_workflow_dispatch()
            elif ident == "github-load-logs":
                self._request_logs()
            elif ident in ("github-workflows-prev","github-workflows-next","github-runs-prev","github-runs-next"):
                page = "_workflow_page" if "workflows" in ident else "_run_page"
                setattr(self,page,max(0,getattr(self,page)+(1 if ident.endswith("next") else -1)))
                self._render_workspace()
            event.stop()

        def on_input_changed(self,event:Input.Changed) -> None:
            ident = event.input.id
            if ident == "github-search":
                self._query = str(event.value or "")
                self._workflow_page = self._run_page = 0
                self._render_workspace()
                event.stop()
            elif ident == "github-log-filter":
                self._log_filter = str(event.value or "")
                self._log_fingerprint = None
                self._render_logs()
                event.stop()

        def on_data_table_row_highlighted(self,event:DataTable.RowHighlighted) -> None:
            if self._draw_tables or not self._live:
                return
            key = str(getattr(event.row_key,"value",event.row_key))
            table_id = event.data_table.id or ""
            if table_id == "github-workflows" and key.startswith("workflow-"):
                value = key[len("workflow-"):]
                if value.isdigit() and int(value) != self._workflow:
                    self._workflow = int(value)
                    self._run_page = 0
                    self._render_workspace()
                event.stop()
            elif table_id == "github-runs" and key.startswith("run-"):
                value = key[len("run-"):]
                if value.isdigit() and int(value) != self._selected_run:
                    self._selected_run = int(value)
                    self._log_preview = None
                    self._render_workspace()
                    if not self._loading:
                        self.request_refresh()
                event.stop()

    return GithubWorkspace


__all__ = ["create_github_workspace_widget"]
