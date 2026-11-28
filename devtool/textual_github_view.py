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
    load_github_actions, read_github_log_preview, read_github_job_logs, search_actions,
)


def create_github_workspace_widget(repo_root: str | Path, *,
        loader: Any = None, log_reader: Any = None, job_log_reader: Any = None,
        run_controller: Any = None, workflow_dispatcher: Any = None,
        refresh_seconds: float = 45.0, auth_mode: str = "app"):
    """Lazily create a Textual widget; no full-screen launcher hosting."""
    from textual.containers import Horizontal, HorizontalScroll, Vertical
    from textual.css.query import NoMatches
    from textual.screen import ModalScreen
    from textual.widget import Widget
    from textual.widgets import Button, DataTable, Input, Label, RichLog, Static, Tree

    root = Path(repo_root).expanduser().resolve(strict=False)
    load = loader or load_github_actions
    from functools import partial
    from rich.text import Text
    from .workspace_viewport import LogCursor, log_page
    load_logs = log_reader or partial(read_github_log_preview, auth_mode=auth_mode)
    load_job_logs = job_log_reader or partial(read_github_job_logs, auth_mode=auth_mode)
    control = run_controller or partial(control_github_run, auth_mode=auth_mode)
    dispatch = workflow_dispatcher or partial(dispatch_github_workflow, auth_mode=auth_mode)

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
        #github-job-detail { height: auto; max-height: 5; }
        #github-log-lines { height: 2fr; min-height: 3; border: round $panel; }
        #github-status { height: auto; min-height: 1; max-height: 3; color: $text-muted; }
        .compact #github-body { layout: vertical; height: auto; width: 100%; }
        .compact #github-controls, .compact #github-log-controls { layout: vertical; height: auto; }
        .compact #github-controls Button, .compact #github-log-controls Button { width: 100%; margin-left: 0; }
        .compact #github-controls Input, .compact #github-log-controls Input { width: 100%; height: 3; }
        .compact #github-control-actions { layout: vertical; height: auto; }
        .compact #github-control-actions Button { width: 100%; margin-left: 0; }
        .compact #github-jobs { height: 8; }
        .compact #github-log-lines { height: 8; }
        .compact #github-lists, .compact #github-detail { width: 100%; border-left: none; }
        .compact #github-lists { height: auto; }
        .compact #github-workflows, .compact #github-runs { height: 6; }
        .compact #github-detail { height: auto; border-top: solid $panel; }
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
            self._selection_revision = 0
            self._native_checks = False
            self._last_fetch = 0.0
            self._selected_run = 0
            self._run_identity: dict[str, Any] | None = None
            self._workflow_identity: dict[str, Any] | None = None
            self._selected_job = 0
            self._selected_step = 0
            self._log_page = 0
            self._log_anchors = [LogCursor()]
            self._log_next = None
            self._log_width = 0
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
            self._compact_geometry: bool | None = None

        def compose(self):
            yield Label("GITHUB ACTIONS   ·   inspect workflows, runs, attempts, jobs and logs", id="github-caption")
            with HorizontalScroll(id="github-controls"):
                yield Input(placeholder="Find a workflow, branch, SHA or run…", id="github-search")
                yield Button("Refresh", id="github-refresh", variant="primary")
                yield Button("Dispatch…", id="github-dispatch")
                yield Button("Native PR checks", id="github-native-checks")
            with Horizontal(id="github-body"):
                with Vertical(id="github-lists"):
                    yield Static("WORKFLOWS  ·  select a filter", classes="github-group-title")
                    yield DataTable(id="github-workflows")
                    with Horizontal(classes="github-pager"):
                        yield Button("< Previous", id="github-workflows-prev")
                        yield Button("Next >", id="github-workflows-next")
                        yield Static("", id="github-workflows-count", classes="github-page-number")
                    yield Static("RUNS · selected workflow / recent", classes="github-group-title")
                    yield DataTable(id="github-runs")
                    with Horizontal(classes="github-pager"):
                        yield Button("< Previous", id="github-runs-prev")
                        yield Button("Next >", id="github-runs-next")
                        yield Static("", id="github-runs-count", classes="github-page-number")
                with Vertical(id="github-detail"):
                    yield Static("Select a run to inspect its current attempt", id="github-selected")
                    yield Tree("EXACT ATTEMPT JOBS", id="github-jobs")
                    yield Static("Select a job or step", id="github-job-detail")
                    with HorizontalScroll(id="github-log-controls"):
                        yield Input(placeholder="Filter fetched log lines…", id="github-log-filter")
                        yield Button("Fetch run logs", id="github-load-logs")
                        yield Button("Fetch job logs", id="github-load-job-logs")
                    with Horizontal(classes="github-pager"):
                        yield Button("< Previous", id="github-logs-prev")
                        yield Button("Next >", id="github-logs-next")
                        yield Static("", id="github-logs-count", classes="github-page-number")
                    with HorizontalScroll(id="github-control-actions"):
                        yield Button("Cancel run…", id="github-cancel")
                        yield Button("Rerun…", id="github-rerun")
                    yield RichLog(id="github-log-lines", wrap=True, highlight=False,
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
            self.call_after_refresh(self._apply_geometry)
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
                self.request_refresh(journal_only=self._native_checks and bool(self._selected_run))

        def request_refresh(self, *, journal_only: bool = False) -> None:
            if not self._live or self._loading:
                return
            self._loading = True
            revision = self._selection_revision
            self._generation += 1
            generation, selected = self._generation, self._selected_run
            workflow = self._workflow
            selection = ActionsSelection("", workflow_id=workflow or None, run_id=selected or None, limit=100,
                run_identity=self._run_identity, workflow_identity=self._workflow_identity, auth_mode=auth_mode, native_checks=self._native_checks, journal_only=journal_only)
            self._render_workspace()
            self._status("Refreshing canonical GitHub Actions data…")
            try:
                self.run_worker(lambda: self._background(generation, selected, workflow, selection, revision),
                                thread=True, exclusive=True, group="github-workspace-read")
            except Exception as exc:
                self._loading = False
                self._stale = True
                self._error = "Cannot start GitHub fetch: " + type(exc).__name__
                self._render_workspace()

        def _background(self, generation: int, selected: int, workflow: int, selection: ActionsSelection, revision: int) -> None:
            try:
                data = load(root, selection=selection)
                error = ""
            except Exception as exc:
                data, error = {}, "GitHub read failed (" + type(exc).__name__ + ")"
            try:
                self.app.call_from_thread(self._finish, generation, selected, workflow, data, error, revision)
            except (RuntimeError, AttributeError):
                pass

        def _finish(self, generation: int, selected: int, workflow: int, data: Mapping[str, Any], error: str, revision: int) -> None:
            if not self._live or generation != self._generation:
                return
            self._loading = False
            if revision != self._selection_revision or (selected, workflow) != (self._selected_run, self._workflow):
                self.request_refresh()
                return
            self._last_fetch = time.monotonic()
            self._error = error
            self._stale = bool(error)
            if error:
                self._clear_logs()
            if not error:
                if data.get("read_only") is not True or data.get("release_qualified") is not False:
                    self._stale = True
                    self._error = "Untrusted Actions projection contract"
                else:
                    verified = data.get("selected_run")
                    if data.get("journal_only"):
                        # Only the journal was reread: retain the previously
                        # verified jobs and never claim a new provider read.
                        data = {**self._snapshot, **data}
                    self._snapshot = dict(data)
                    if selected and (not isinstance(verified, Mapping) or verified.get("id") != selected):
                        self._snapshot["jobs"] = []
                        self._stale = True
                        self._error = "Selected run detail not bound to current selection"
                    elif selected:
                        self._run_identity = dict(verified)
            self._render_workspace()
            # Selection may change while the previous request is pending. Do
            # not let an earlier run's jobs remain visible under the new run.
            if selected != self._selected_run or workflow != self._workflow:
                self.request_refresh()

        def _replace_table(self, selector: str, rows: list[tuple[str, ...]],
                           identities: list[str]) -> None:
            table = self.query_one(selector, DataTable)
            compact = self.size.width < 72
            fingerprint = (tuple(rows), tuple(identities), compact, self.size.width if compact else 0)
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
                table.clear(columns=True)
                if compact:
                    table.add_column("Workflow" if selector.endswith("workflows") else "Run · status", width=max(4,self.size.width-8))
                else:
                    table.add_columns(*(("Workflow", "State", "ID") if selector.endswith("workflows") else ("Run", "Status", "Branch", "SHA")))
                for row, identity in zip(rows, identities):
                    cells = [Text(" · ".join(row), no_wrap=True, overflow="ellipsis")] if compact else [Text(x) for x in row]
                    table.add_row(*cells, key=identity)
                if rows:
                    chosen = identities.index(previous) if previous in identities else min(cursor, len(rows)-1)
                    table.move_cursor(row=chosen, column=0, animate=False)
            finally:
                self._draw_tables = False
            self._fingerprints[selector] = fingerprint

        def _apply_geometry(self) -> None:
            if not self._live:
                return
            compact = self.size.width < 72
            if compact == self._compact_geometry:
                return
            self._compact_geometry = compact
            self.set_class(compact, "compact")
            # Textual's ancestor-class CSS can be applied incrementally while
            # a subtree is mounting. Explicit layout properties keep all
            # panes visible through the same resize, not just the first child.
            for selector, values in {
                "#github-body": {"layout": "vertical", "height": "auto", "width": "100%"},
                "#github-lists": {"height": "auto", "width": "100%"},
                "#github-detail": {"height": "auto", "width": "100%", "border_left": ("none", "transparent")},
                "#github-workflows": {"height": 6}, "#github-runs": {"height": 6},
                "#github-jobs": {"height": 8}, "#github-log-lines": {"height": 8},
                "#github-controls": {"layout": "vertical", "height": "auto"},
                "#github-log-controls": {"layout": "vertical", "height": "auto"},
                "#github-control-actions": {"layout": "vertical", "height": "auto"},
            }.items():
                styles = self.query_one(selector).styles
                for name, value in values.items():
                    if compact:
                        setattr(styles, name, value)
                    else:
                        styles.clear_rule(name)
            for selector in ("#github-controls", "#github-log-controls", "#github-control-actions"):
                for widget in self.query_one(selector).children:
                    if compact:
                        widget.styles.width = "100%"
                        widget.styles.margin = 0
                    else:
                        widget.styles.clear_rule("width")
                        widget.styles.clear_rule("margin")
            self.call_after_refresh(self._render_workspace)

        def on_resize(self, event: Any) -> None:
            if self._live:
                self.call_after_refresh(self._apply_geometry)
                self.call_after_refresh(self._render_workspace)

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
                self.query_one("#github-selected",Static).update(Text(label))
                jobs = data.get("jobs") if bound and data.get("jobs_scope")=="exact-run-attempt" else []
                fingerprint = (self._selected_run, repr(jobs), data.get("jobs_scope"))
                tree = self.query_one("#github-jobs",Tree)
                if self._tree_fingerprint != fingerprint:
                    tree.clear()
                    tree.root.set_label("Exact attempt jobs" if bound else "Jobs · select a run")
                    restore = tree.root
                    found = False
                    for job in jobs if isinstance(jobs,list) else []:
                        jid = job.get("id")
                        node = tree.root.add(Text(str(job.get("name") or jid)+" ["+display_status(job)+"]"), data=(jid, 0))
                        if jid == self._selected_job:
                            restore = node
                            found = True
                        for step in job.get("steps") or []:
                            number = step.get("number")
                            leaf = node.add_leaf(Text(str(step.get("name") or "-")+" ["+display_status(step)+"]"), data=(jid, number))
                            if jid == self._selected_job and number == self._selected_step:
                                restore = leaf
                        node.expand()
                    tree.root.expand()
                    if not found and self._selected_job:
                        self._selected_job = self._selected_step = 0
                        self._clear_logs()
                    tree.move_cursor(restore)
                    self._tree_fingerprint = fingerprint
                self._render_job_detail()
                can_control = bool(bound and selected.get("run_attempt") and selected.get("sha")) and not self._remote_loading and not self._stale and not self._loading
                self.query_one("#github-load-logs",Button).disabled = not can_control or self._log_loading
                self.query_one("#github-load-job-logs",Button).disabled = not can_control or self._log_loading or self._job() is None
                self.query_one("#github-cancel",Button).disabled = not can_control or self._native_checks
                self.query_one("#github-rerun",Button).disabled = not can_control or self._native_checks
                self.query_one("#github-dispatch",Button).disabled = self._native_checks or not self._workflow or self._remote_loading or self._stale or self._loading
                self._render_logs()
                flags=["native session observation · GET only"] if self._native_checks else []
                if any(j.get("steps_truncated") for j in jobs or []):
                    flags.append("step list truncated")
                if data.get("journal_only"):
                    flags.append("jobs from last explicit read")
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
                    f"{data.get('repository') or 'GitHub'} · auth {data.get('authentication') or auth_mode} · {len(runs)} visible runs · NOT CI evidence · remote state unqualified"+suffix)
            except NoMatches:
                self._live = False

        def _status(self, msg: str) -> None:
            try:
                self.query_one("#github-status",Static).update(Text(msg))
            except NoMatches:
                pass

        def _job(self) -> Mapping[str, Any] | None:
            selected = self._snapshot.get("selected_run")
            if not isinstance(selected, Mapping) or selected.get("id") != self._selected_run:
                return None
            return next((job for job in self._snapshot.get("jobs", [])
                         if job.get("id") == self._selected_job), None)

        def _clear_logs(self) -> None:
            self._log_generation += 1
            self._log_loading = False
            self._log_preview = None
            self._log_page = 0
            self._log_anchors = [LogCursor()]
            self._log_fingerprint = None

        def _render_job_detail(self) -> None:
            job = self._job()
            step = next((x for x in job.get("steps", []) if x.get("number") == self._selected_step), None) if job else None
            if step:
                label = f"Job {job['id']} · step {step['number']} · {display_status(step)}\n{step.get('name') or '-'}\n{step.get('started_at') or '-'} → {step.get('completed_at') or '-'}"
            elif job:
                label = f"Job {job['id']} · {display_status(job)} · {job.get('name') or '-'}"
            else:
                label = "Select a job or step for exact details and job logs"
            self.query_one("#github-job-detail", Static).update(Text(label))

        def on_tree_node_selected(self, event: Tree.NodeSelected) -> None:
            if event.control.id != "github-jobs" or not self._live:
                return
            data = event.node.data
            jid, step = data if isinstance(data, tuple) and len(data) == 2 else (0, 0)
            if jid != self._selected_job:
                self._clear_logs()
            self._selected_job = jid
            self._selected_step = step
            self._render_job_detail()
            self._render_logs()
            self.query_one("#github-load-job-logs", Button).disabled = self._stale or self._loading or self._log_loading or self._job() is None
            event.stop()

        def _render_logs(self) -> None:
            log = self.query_one("#github-log-lines",RichLog)
            preview = self._log_preview
            selected = self._snapshot.get("selected_run")
            bound = (isinstance(selected,Mapping) and selected.get("id") == self._selected_run)
            valid = (bound and preview and preview.get("run_id") == self._selected_run
                     and preview.get("run_attempt") == selected.get("run_attempt")
                     and preview.get("sha") == str(selected.get("sha") or "").lower()
                     and (not preview.get("job_id") or preview.get("job_id") == self._selected_job))
            lines = preview.get("lines",[]) if valid else []
            term = self._log_filter.casefold().strip()
            matches = [line for line in lines if term in line.casefold()] if term else lines
            # Use the shared grapheme-aware source cursor, including long lines.
            width = max(4, log.size.width - 4)
            if width != self._log_width:
                self._log_width = width
                self._log_anchors = self._log_anchors[:self._log_page+1]
            self._log_page = min(self._log_page, len(self._log_anchors)-1)
            cursor = self._log_anchors[self._log_page]
            visible, self._log_next = log_page(matches, cursor, width, 100)
            self.query_one("#github-logs-prev",Button).disabled = self._log_page == 0
            self.query_one("#github-logs-next",Button).disabled = self._log_next is None
            self.query_one("#github-logs-count",Static).update(f"Page {self._log_page+1} · line {min(cursor.line+1,len(matches))}/{len(matches)}")
            fingerprint = (self._selected_run, selected.get("run_attempt") if bound else None,
                           self._selected_job, tuple(visible))
            if fingerprint == self._log_fingerprint:
                return
            log.clear()
            for line in visible:
                log.write(Text(line))
            self._shown_logs = list(visible)
            self._log_fingerprint = fingerprint

        def _request_logs(self, *, job_only: bool = False) -> None:
            if self._log_loading or not self._live or self._stale or self._loading:
                return
            selected = self._snapshot.get("selected_run")
            if not isinstance(selected,Mapping) or selected.get("id") != self._selected_run:
                self._status("Select a verified run before fetching logs")
                return
            attempt, sha = selected.get("run_attempt"), str(selected.get("sha") or "")
            if type(attempt) is not int or attempt <= 0 or len(sha) != 40:
                self._status("Run lacks a verified attempt or HEAD; logs unavailable")
                return
            job = self._job() if job_only else None
            if job_only and job is None:
                self._status("Select a verified job first")
                return
            self._log_loading = True
            self._log_generation += 1
            generation = self._log_generation
            identity = dict(selected)
            self._status(f"Fetching {'job' if job else 'run'} logs for run {identity['id']}, attempt {attempt} via canonical safe downloader…")
            self.query_one("#github-load-logs",Button).disabled = True
            self.query_one("#github-load-job-logs",Button).disabled = True
            try:
                self.run_worker(lambda: self._logs_background(generation, identity, dict(job) if job else None),
                                thread=True, exclusive=True, group="github-workspace-logs")
            except Exception as exc:
                self._log_loading = False
                self._status("Log worker unavailable ("+type(exc).__name__+")")

        def _logs_background(self, generation: int, identity: Mapping[str, Any], job: Mapping[str, Any] | None) -> None:
            try:
                if job:
                    preview = load_job_logs(root, run=identity, job=job)
                else:
                    kwargs = dict(run_id=identity["id"], attempt=identity["run_attempt"], head_sha=identity["sha"])
                    if log_reader is None:
                        kwargs["run"] = identity
                    preview = load_logs(root, **kwargs)
                error = ""
            except Exception as exc:
                preview, error = {}, "Log preview unavailable ("+type(exc).__name__+")"
            try:
                self.app.call_from_thread(self._finish_logs,generation,identity,job,preview,error)
            except (RuntimeError,AttributeError):
                pass

        def _finish_logs(self,generation:int,identity:Mapping[str,Any],job:Mapping[str,Any]|None,preview:Mapping[str,Any],error:str) -> None:
            if not self._live or generation != self._log_generation:
                return
            self._log_loading = False
            selected = self._snapshot.get("selected_run")
            if (error or self._stale or not isinstance(selected,Mapping) or selected.get("id") != self._selected_run
                    or any(identity.get(k) != selected.get(k) for k in ("id","run_attempt","sha","workflow_id","event","native_context"))
                    or preview.get("run_id") != self._selected_run
                    or preview.get("run_attempt") != selected.get("run_attempt")
                    or preview.get("sha") != str(selected.get("sha") or "").lower()
                    or (job and (job.get("id") != self._selected_job or preview.get("job_id") != self._selected_job))):
                self._log_preview = None
                self._render_logs()
                self._status(error or "Run or job changed while logs loaded; refresh and retry")
            else:
                self._log_preview = dict(preview)
                self._log_fingerprint = None
                self._log_page = 0
                self._log_anchors = [LogCursor()]
                self._render_logs()
                self._status(f"Log preview · {len(preview.get('lines',[]))} bounded lines"
                             + (" · TRUNCATED" if preview.get("truncated") else "")
                             + " · not qualification evidence")
            self.query_one("#github-load-logs",Button).disabled = self._stale or self._loading
            self.query_one("#github-load-job-logs",Button).disabled = self._stale or self._loading or self._job() is None

        def _confirm_run_control(self, action: str) -> None:
            if self._native_checks or self._remote_loading or self._stale or self._loading:
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
            revision = self._selection_revision
            self.app.push_screen(RunControlModal(prompt),
                lambda answer: self._send_remote("run",identity,attempt,sha,action)
                if answer and revision == self._selection_revision else None)

        def _confirm_workflow_dispatch(self) -> None:
            if self._native_checks or self._remote_loading or self._stale or self._loading or not self._workflow:
                self._status("Select an exact workflow before dispatch")
                return
            workflow=self._workflow
            selected=self._snapshot.get("selected_run")
            default_ref=str(selected.get("branch") or "") if isinstance(selected,Mapping) else ""
            # Reference is entered/confirmed explicitly, never guessed from
            # an unrelated workflow's latest run.
            revision = self._selection_revision
            self.app.push_screen(WorkflowDispatchModal(workflow,default_ref),
                lambda answer: self._send_remote("dispatch",workflow,answer["ref"],answer["inputs"])
                if isinstance(answer,dict) and revision == self._selection_revision else None)

        def _send_remote(self, kind: str, *args: Any) -> None:
            if not self._live or self._native_checks or self._remote_loading or self._stale or self._loading:
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
                if self._stale:
                    # Recover from a superseded attempt with a fresh inventory;
                    # the user must explicitly choose the new attempt again.
                    self._selection_revision += 1
                    self._selected_run = self._selected_job = self._selected_step = 0
                    self._run_identity = None
                    self._snapshot.pop("selected_run", None)
                    self._snapshot.pop("journal_only", None)
                    self._snapshot["jobs"] = []
                    self._clear_logs()
                self.request_refresh()
            elif ident == "github-native-checks":
                if not self._remote_loading:
                    self._native_checks = not self._native_checks
                    self._selection_revision += 1
                    self._snapshot = {}
                    self._selected_run = self._workflow = self._selected_job = self._selected_step = 0
                    self._run_identity = self._workflow_identity = None
                    self._workflow_page = self._run_page = 0
                    self._clear_logs()
                    event.button.label = "Repository Actions" if self._native_checks else "Native PR checks"
                    self._render_workspace()
                    self.request_refresh()
            elif ident == "github-cancel":
                self._confirm_run_control("cancel")
            elif ident == "github-rerun":
                self._confirm_run_control("rerun")
            elif ident == "github-dispatch":
                self._confirm_workflow_dispatch()
            elif ident == "github-load-logs":
                self._request_logs()
            elif ident == "github-load-job-logs":
                self._request_logs(job_only=True)
            elif ident in ("github-logs-prev", "github-logs-next"):
                if ident.endswith("next") and self._log_next is not None:
                    self._log_anchors = self._log_anchors[:self._log_page+1] + [self._log_next]
                    self._log_page += 1
                elif ident.endswith("prev"):
                    self._log_page = max(0,self._log_page-1)
                self._render_logs()
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
                self._log_page = 0
                self._log_anchors = [LogCursor()]
                self._log_fingerprint = None
                self._render_logs()
                event.stop()

        def on_data_table_row_selected(self,event:DataTable.RowSelected) -> None:
            if self._draw_tables or not self._live:
                return
            key = str(getattr(event.row_key,"value",event.row_key))
            table_id = event.data_table.id or ""
            if table_id == "github-workflows" and key.startswith("workflow-") and not self._native_checks:
                value = key[len("workflow-"):]
                if value.isdigit() and int(value) != self._workflow:
                    self._selection_revision += 1
                    self._workflow = int(value)
                    self._workflow_identity = next((dict(x) for x in self._snapshot.get("workflows", []) if x.get("id") == self._workflow), None)
                    self._selected_run = self._selected_job = self._selected_step = 0
                    self._run_identity = None
                    self._clear_logs()
                    self._run_page = 0
                    self._render_workspace()
                    self.request_refresh()
                event.stop()
            elif table_id == "github-runs" and key.startswith("run-"):
                value = key[len("run-"):]
                if value.isdigit() and (int(value) != self._selected_run or self._stale):
                    self._selection_revision += 1
                    self._selected_run = int(value)
                    self._run_identity = next((dict(x) for x in self._snapshot.get("runs", []) if x.get("id") == self._selected_run and x.get("run_attempt") and x.get("head_sha")), None)
                    self._selected_job = self._selected_step = 0
                    self._snapshot.pop("selected_run", None)
                    self._snapshot.pop("journal_only", None)
                    self._snapshot["jobs"] = []
                    self._clear_logs()
                    self._render_workspace()
                    if not self._loading:
                        self.request_refresh()
                event.stop()

    return GithubWorkspace


__all__ = ["create_github_workspace_widget"]
