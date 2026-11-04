# DN03-R5 — RM-09 selected-project source admission

`ci_dotnet_selected_project_only = true` is a job-local, explicit opt-in.
It admits a **single tracked project and its transitive tracked ProjectReferences**
while retaining the same Git commit/graph, global.json, NuGet lockfile and
static Directory.Build.props contract. Unrelated projects, solutions and
Windows-specific game targets cannot poison unrelated native test plans.

The default remains the original complete-repository source audit.
Untracked project references, invalid path escapes, dynamic props in the
selected closure, malformed or missing NuGet locks, or a non-boolean opt-in
are refused. Tests run only on GitHub-hosted .NET environments.

This source change does **not** by itself prove the generated RM-09 CI has run.
Its downstream consumer is `Mikeyphw/Mike_SVMods` and must independently
generate/commit a DN03-R4 workflow pinned to the *pushed exact Devtool SHA*.
