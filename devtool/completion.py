from __future__ import annotations

ZSH_COMPLETION = r'''#compdef devtool

# Legacy desktop page order marker for regression compatibility: overview runs history diagnostics repositories targets run settings
# Devtool zsh completion. Keep this file synchronized with devtool/completion.py.
# Hidden execution provenance controls intentionally not offered as candidates: --internal-invocation-source --internal-invocation-command --internal-wrapper-schema

_devtool_repo_path() {
  if (( $+commands[fzf] )); then
    local selected
    selected=$(find . -maxdepth 4 -type d 2>/dev/null | fzf --prompt='repo> ' --height=40% --reverse) || return 1
    compadd -- "$selected"
  else
    _files -/
  fi
}

_devtool_artifact_path() {
  if (( $+commands[fzf] )); then
    local selected
    selected=$(find ~/Downloads . -maxdepth 4 -type f \
      \( -name '*.zip' -o -name '*.tar' -o -name '*.tar.gz' -o -name '*.tgz' \
         -o -name '*.tar.xz' -o -name '*.txz' -o -name '*.tar.zst' -o -name '*.tzst' \
         -o -name '*.tar.bz2' -o -name '*.tbz2' \) 2>/dev/null |
      fzf --prompt='artifact> ' --height=40% --reverse) || return 1
    compadd -- "$selected"
  else
    _files
  fi
}

_devtool_completion_values() {
  local kind=$1 repo target
  repo=${opt_args[--repo]:-${opt_args[-r]:-.}}
  target=${opt_args[--target]:-}
  local -a cmd
  cmd=(devtool --repo "$repo")
  if [[ -n $target ]]; then
    cmd+=(--target "$target")
  fi
  "${cmd[@]}" completion-state --kind "$kind" 2>/dev/null
}

_devtool_wrapper_commands() {
  local -a commands aliases values
  commands=(${(f)"$(_devtool_completion_values wrapper-commands)"})
  aliases=(${(f)"$(_devtool_completion_values wrapper-aliases)"})
  values=($commands $aliases)
  values=(${(u)values})
  if (( ${#values} )); then
    _describe -t wrapper-commands 'repository wrapper command' values
  else
    _message 'repository wrapper command from [wrapper.commands]'
  fi
}

_devtool_wrapper_options() {
  local command=$1 item
  local -a raw options
  raw=(${(f)"$(_devtool_completion_values wrapper-options)"})
  options=(--help --version)
  for item in $raw; do
    if [[ $item == "$command:"* ]]; then
      options+=("${item#*:}")
    fi
  done
  options=(${(u)options})
  _values 'repository wrapper option' $options
}

_devtool_federation_repositories() {
  local -a values
  values=(${(f)"$(_devtool_completion_values federation-repositories)"})
  if (( ${#values} )); then
    _describe -t federation-repositories 'federated repository' values
  else
    _message 'repository from [federation.repositories]'
  fi
}

_devtool_federation_workflows() {
  local -a values
  values=(${(f)"$(_devtool_completion_values federation-workflows)"})
  if (( ${#values} )); then
    _describe -t federation-workflows 'federation workflow' values
  else
    _message 'workflow from [federation.workflows]'
  fi
}

_devtool_federation_groups() {
  local -a values
  values=(${(f)"$(_devtool_completion_values federation-executable-groups)"})
  if (( ${#values} )); then
    _describe -t federation-groups 'executable federation group' values
  else
    _message 'executable group from [federation.groups]'
  fi
}

_devtool_targets() {
  local repo config
  local -a targets
  repo=${opt_args[--repo]:-${opt_args[-r]:-.}}
  config=${opt_args[--config]:-${opt_args[-c]:-$repo/.devtool.toml}}
  if [[ -r $config ]]; then
    targets=(${(f)"$(_devtool_completion_values targets)"})
    if (( ! ${#targets} )); then
      targets=(${(f)"$(sed -nE 's/^\[targets\.([^].]+)\].*/\1/p' "$config" 2>/dev/null)"})
    fi
  fi
  if (( ${#targets} )); then
    _describe -t targets 'target' targets
  else
    _message 'target name from .devtool.toml'
  fi
}

_devtool_groups() {
  local repo config
  local -a groups
  repo=${opt_args[--repo]:-${opt_args[-r]:-.}}
  config=${opt_args[--config]:-${opt_args[-c]:-$repo/.devtool.toml}}
  groups=(${(f)"$(_devtool_completion_values groups)"})
  if (( ! ${#groups} )) && [[ -r $config ]]; then
    groups=(${(f)"$(awk '
      /^\[workspace\.groups\]$/ { in_groups=1; next }
      /^\[/ { in_groups=0 }
      in_groups && $0 ~ /^[[:space:]]*[A-Za-z0-9_.-]+[[:space:]]*=/ { line=$0; sub(/^[[:space:]]*/, "", line); sub(/[[:space:]]*=.*/, "", line); print line }
    ' "$config" 2>/dev/null)"})
  fi
  if (( ${#groups} )); then
    _describe -t groups 'workspace group' groups
  else
    _message 'workspace group from [workspace.groups]'
  fi
}

_devtool_workflow_actions() {
  local -a actions declared
  actions=(restore build test lint package run validate wine-validate)
  declared=(${(f)"$(_devtool_completion_values workflows)"})
  declared=(${declared:#*:*})
  actions+=(${declared})
  actions=(${(u)actions})
  if (( ${#actions} )); then
    _describe -t workflow-actions 'workflow action' actions
  else
    _message 'declared workflow action'
  fi
}

_devtool_ui_profiles() {
  local repo config global_config
  local -a profiles
  repo=${opt_args[--repo]:-${opt_args[-r]:-.}}
  config=${opt_args[--config]:-${opt_args[-c]:-$repo/.devtool.toml}}
  global_config=${DEVTOOL_GLOBAL_CONFIG:-${XDG_CONFIG_HOME:-$HOME/.config}/devtool/config.toml}
  profiles=(${(f)"$(_devtool_completion_values ui-profiles)"})
  (( ${#profiles} )) || profiles=(auto minimal standard developer debug)
  if [[ -r $global_config ]]; then
    profiles+=(${(f)"$(sed -nE 's/^\[ui\.profiles\.([^].]+)\].*/\1/p' "$global_config" 2>/dev/null)"})
  fi
  if [[ -r $config ]]; then
    profiles+=(${(f)"$(sed -nE 's/^\[ui\.profiles\.([^].]+)\].*/\1/p' "$config" 2>/dev/null)"})
  fi
  profiles=(${(u)profiles})
  _describe -t ui-profiles 'UI profile' profiles
}

_devtool_ssh_profiles() {
  local repo config global_config
  local -a profiles
  repo=${opt_args[--repo]:-${opt_args[-r]:-.}}
  config=${opt_args[--config]:-${opt_args[-c]:-$repo/.devtool.toml}}
  global_config=${DEVTOOL_GLOBAL_CONFIG:-${XDG_CONFIG_HOME:-$HOME/.config}/devtool/config.toml}
  if [[ -r $global_config ]]; then
    profiles+=(${(f)"$(sed -nE 's/^\[ssh\.hosts\.([^].]+)\].*/\1/p' "$global_config" 2>/dev/null)"})
  fi
  if [[ -r $config ]]; then
    profiles+=(${(f)"$(sed -nE 's/^\[ssh\.hosts\.([^].]+)\].*/\1/p' "$config" 2>/dev/null)"})
  fi
  profiles=(${(u)profiles})
  if (( ${#profiles} )); then
    _describe -t ssh-profiles 'SSH worker profile' profiles
  else
    _message 'named SSH worker profile'
  fi
}

_devtool_test_profiles() {
  local repo config
  local -a profiles
  repo=${opt_args[--repo]:-${opt_args[-r]:-.}}
  config=${opt_args[--config]:-${opt_args[-c]:-$repo/.devtool.toml}}
  profiles=(${(f)"$(_devtool_completion_values test-profiles)"})
  if (( ! ${#profiles} )) && [[ -r $config ]]; then
    profiles=(${(fu)"$(sed -nE \
      -e 's/^\[test_profiles\.([^].]+)\].*/\1/p' \
      -e 's/^\[targets\.[^]]+\.test_profiles\.([^].]+)\].*/\1/p' \
      "$config" 2>/dev/null)"})
  fi
  if (( ${#profiles} )); then
    _describe -t test-profiles 'test profile' profiles
  else
    _message 'saved test profile'
  fi
}

_devtool_variants() {
  local repo config
  local -a variants
  repo=${opt_args[--repo]:-${opt_args[-r]:-.}}
  config=${opt_args[--config]:-${opt_args[-c]:-$repo/.devtool.toml}}
  variants=(${(f)"$(_devtool_completion_values variants)"})
  if (( ! ${#variants} )) && [[ -r $config ]]; then
    variants=(${(fu)"$(sed -nE \
      -e 's/^\[variants\.([^].]+)\].*/\1/p' \
      -e 's/^\[targets\.[^]]+\.variants\.([^].]+)(\.|]).*/\1/p' \
      "$config" 2>/dev/null)"})
  fi
  if (( ${#variants} )); then
    _describe -t variants 'variant' variants
  else
    _message 'build/test variant'
  fi
}

_devtool_pytest_tests() {
  local -a tests
  tests=(${(f)"$(_devtool_completion_values pytest-tests)"})
  if (( ${#tests} )); then
    _describe -t pytest-tests 'pytest node ID' tests
  else
    _message 'pytest node ID or file'
  fi
}

_devtool_steps() {
  local -a steps
  steps=(
    'restore:restore dependencies'
    'build:build project'
    'test:run tests'
    'lint:run lint and static analysis'
    'validate:run the target validation pipeline'
    'validate-all:run validation and collect all independent failures'
    'wine-validate:validate with Wine environment settings'
    'run:run project'
    'package:package project'
    'release:run Android release pipeline and seal evidence'
    'clean:clean generated outputs'
    'apply-overlay=:apply one overlay artifact'
    'apply-overlays=:apply an overlay bundle or ordered overlays'
    'apply-snapshot=:apply a repository snapshot'
    'commit=:create a Git commit'
    'tag=:create a Git tag'
  )
  _describe -t steps 'batch step' steps
}

_devtool_commands() {
  local -a commands
  commands=(
    'restore:restore project dependencies'
    'rs:alias for restore'
    'build:build the selected project'
    'b:alias for build'
    'test:run or discover selected tests'
    't:alias for test'
    'lint:run configured lint and static-analysis tools'
    'validate:run runner-specific validation'
    'v:alias for validate'
    'validate-all:run runner-specific validation and collect all independent failures'
    'validation-menu:open the interactive validation cockpit'
    'vm:alias for validation-menu'
    'menu:open an interactive Devtool menu'
    'start-validation:start a persistent sequential validation session'
    'sv:alias for start-validation'
    'resume-validation:resume a persistent sequential validation session'
    'rv:alias for resume-validation'
    'validation-status:show the active validation session'
    'vs:alias for validation-status'
    'validation-plan:show the durable validation phase plan'
    'vplan:alias for validation-plan'
    'validation-pause:pause after the current phase'
    'vpause:alias for validation-pause'
    'validation-stop:stop the current phase and keep it retryable'
    'vstop:alias for validation-stop'
    'validation-events:read durable validation events'
    'vevents:alias for validation-events'
    'validation-report:show the durable validation report'
    'vreport:alias for validation-report'
    'validation-finalize:finalize a completed validation session'
    'vf:alias for validation-finalize'
    'validation-abort:abort and optionally roll back a validation session'
    'va:alias for validation-abort'
    'validation-recover:recover an interrupted phase or resume a rollback'
    'vrec:alias for validation-recover'
    'validation-reset:reset a phase and its dependent validation verdicts'
    'vreset:alias for validation-reset'
    'validation-accept-plan-change:accept an incompatible validation plan change with an audit reason'
    'vapc:alias for validation-accept-plan-change'
    'wine-validate:run validation with Wine environment settings'
    'wv:alias for wine-validate'
    'run:run the selected project'
    'rn:alias for run'
    'package:package the selected project'
    'pkg:alias for package'
    'release:run the canonical Android release pipeline'
    'rel:alias for release'
    'clean:clean generated project output'
    'cl:alias for clean'
    'inspect:inspect project type, runner, toolchain, tasks, and artifacts'
    'info:alias for inspect'
    'status:show resolved repository and runner status'
    'st:alias for status'
    'doctor:diagnose devtool and project prerequisites'
    'dr:alias for doctor'
    'ssh:inspect, bootstrap, and synchronize named SSH build workers'
    'oci:manage PRoot-Distro OCI runtime state on named SSH workers'
    'bootstrap:provision reusable destination-specific toolchains'
    'android:inspect Android capabilities, ADB providers, and connected devices'
    'termux:inspect native Termux providers, packages, and PRoot guests'
    'apply-overlay:transactionally apply one overlay'
    'ao:alias for apply-overlay'
    'apply-overlays:transactionally apply a bundle or ordered overlays'
    'aos:alias for apply-overlays'
    'apply-snapshot:transactionally apply a snapshot'
    'as:alias for apply-snapshot'
    'apply:nested apply command'
    'validate-overlay:validate an overlay archive'
    'vo:alias for validate-overlay'
    'validate-overlays:validate a bundle or ordered overlays'
    'vos:alias for validate-overlays'
    'overlay:preflight and rebase overlay artifacts'
    'plugin:discover and diagnose devtool plugins'
    'plugins:alias for plugin'
    'inspect-artifact:inspect overlay artifact metadata, inventory, validation, and diff'
    'ia:alias for inspect-artifact'
    'artifact:inspect and maintain the produced build-artifact registry'
    'tui:open the responsive terminal dashboard'
    'cleanup:archive historical repository backups'
    'bundle:create artifact bundles'
    'batch:run an ordered batch of operations'
    'commit:create a Git commit'
    'ci:alias for commit'
    'tag:create a Git tag'
    'release-notes:generate release notes between Git refs'
    'init-config:create .devtool.toml'
    'init:alias for init-config'
    'config:inspect, initialize, edit, or diagnose configuration'
    'detect:detect project type and targets'
    'targets:list named targets with platform, runner, and workflow metadata'
    'tg:alias for targets'
    'groups:list workspace target groups and expanded members'
    'gr:alias for groups'
    'jobs:list reusable polyglot jobs'
    'jb:alias for jobs'
    'target-local-jobs:report target-local job adoption and workflow wiring'
    'tlj:alias for target-local-jobs'
    'workflows:list configured target workflows and safe scheduling metadata'
    'wf:alias for workflows'
    'invoke:execute an arbitrary declared workflow action'
    'plan:plan expanded workflow DAG, safe scheduling waves, scopes, and commands'
    'pl:alias for plan'
    'affected:show dependency-aware targets affected by Git changes'
    'aff:alias for affected'
    'preflight:check expanded workflow roots, environments, runners, tools, and outputs'
    'pf:alias for preflight'
    'ui-contract:inspect shared UI preferences, capabilities, responsive policy, progress/diagnostics, confirmation/notification policy, tokens, actions, and navigation'
    'uic:alias for ui-contract'
    'logs:list, open, or tail devtool logs'
    'history:list structured run history'
    'rollback:rollback a recorded commit'
    'handoff:generate a repository handoff'
    'ai-prompt:generate an implementation prompt'
    'ap:alias for ai-prompt'
    'completion:print shell completion'
    'completion-state:emit config-aware completion values for shells and editors'
    'git:inspect authoritative repository Git state'
    'wrapper:resolve, inspect, and execute repository wrapper commands'
    'federation:inspect, plan, run, and observe explicitly enrolled repositories'
    'fed:alias for federation'
    'pytest:profile Devtool pytest runtime and slow-test budget behavior'
    'compatibility:audit retained legacy interfaces and migration paths'
    'compat:alias for compatibility'
    'final-seal:run the FS03 repository-wide final release audit'
    'seal:alias for final-seal'
    'hud:run a live HUD renderer'
    'android-hud-bridge:bridge Termux or chroot progress to Android'
    'termux-hud-bridge:alias for android-hud-bridge'
    'control-center:launch the desktop Control Center'
    'gui:alias for control-center'
    'ipc-server:run the Control Center IPC broker'
  )
  _describe -t commands 'devtool command' commands
}

_devtool_test_args() {
  _arguments \
    '--target[named target/profile]:target:_devtool_targets' \
    '*--group[workspace target group]:group:_devtool_groups' \
    '--all-targets[select every workspace target]' \
    '--affected[select dependency-aware affected targets]' \
    '--affected-base[Git base revision for affected selection]:revision:' \
    '--affected-head[Git head revision for affected selection]:revision:' \
    '--variant[target variant]:variant:_devtool_variants' \
    '--no-build[do not rebuild before dotnet test]' \
    '(--list --list-tests)'{--list,--list-tests}'[discover and list tests without executing]' \
    '--profile[saved test profile]:profile:_devtool_test_profiles' \
    '*--select[select a type, suite, tag, class, or name]:selector:' \
    '*--pytest-test[run exactly this pytest file or node ID]:pytest node id:_devtool_pytest_tests' \
    '*--pytest-marker[pytest -m marker expression]:marker expression:' \
    '--pytest-k[pytest -k keyword expression]:keyword expression:' \
    '*--pytest-arg[pass one structured argument directly to pytest]:pytest argument:' \
    '--pytest-include-defaults[keep configured or affected default pytest targets with explicit pytest tests]' \
    '*--id[select an exact framework test ID]:test id:' \
    '*--tag[include a test tag or trait]:tag:' \
    '*--exclude-tag[exclude a test tag or trait]:tag:' \
    '*--path[select tests below a source path]:path:_files' \
    '*--class[select a test class]:class:' \
    '*--tests[select a test class]:class:' \
    '*--method[select a test method]:method:' \
    '--filter[framework-native test filter]:filter:' \
    '--task[Gradle test task override]:task:' \
    '--failed[rerun tests that failed in the previous structured run]' \
    '--changed[select tests related to changed files]' \
    '--changed-from[Git base for changed test selection]:revision:' \
    '--instrumented[run Android instrumentation tests on a device]' \
    '(--workflow-parallel --no-workflow-parallel)--workflow-parallel[enable safe dependency/scope-aware workflow-node parallelism]' \
    '(--workflow-parallel --no-workflow-parallel)--no-workflow-parallel[force sequential workflow-node execution]' \
    '--workflow-max-workers[maximum concurrent workflow nodes]:count:'
}

_devtool_validation_menu_args() {
  _arguments \
    '--no-fzf[use the numbered terminal fallback]' \
    '--runtime-tasks[query Gradle for the complete runtime task catalog]' \
    '--wizard[start directly in the guided validation wizard]' \
    '--recipe[open a built-in or saved validation recipe]:recipe:' \
    '--artifact[artifact used to expand recipe placeholders]:artifact:_devtool_artifact_path' \
    '--matrix-values[comma-separated values used to expand matrix recipes]:values:' \
    '--print-command[print the generated command without opening the review screen]'
}

_devtool_validation_args() {
  _arguments \
    '--target[named target/profile]:target:_devtool_targets' \
    '*--group[workspace target group]:group:_devtool_groups' \
    '--all-targets[select every workspace target]' \
    '--affected[select dependency-aware affected targets]' \
    '--affected-base[Git base revision for affected selection]:revision:' \
    '--affected-head[Git head revision for affected selection]:revision:' \
    '--variant[target variant]:variant:_devtool_variants' \
    '--skip-restore[skip dependency restore]' \
    '--skip-build[skip project build]' \
    '--skip-test[skip project tests]' \
    '--test-profile[saved test profile used by validation]:profile:_devtool_test_profiles' \
    '*--select[select tests by type, suite, tag, class, or name]:selector:' \
    '*--pytest-test[run exactly this pytest file or node ID]:pytest node id:_devtool_pytest_tests' \
    '*--pytest-marker[pytest -m marker expression]:marker expression:' \
    '--pytest-k[pytest -k keyword expression]:keyword expression:' \
    '*--pytest-arg[pass one structured argument directly to pytest]:pytest argument:' \
    '--pytest-include-defaults[keep configured or affected default pytest targets with explicit pytest tests]' \
    '*--id[select an exact framework test ID]:test id:' \
    '*--tag[include a test tag or trait]:tag:' \
    '*--exclude-tag[exclude a test tag or trait]:tag:' \
    '*--path[select tests below a source path or package]:path:_files' \
    '*--class[select a test class]:class:' \
    '*--tests[select a test class]:class:' \
    '*--method[select a test method]:method:' \
    '--filter[framework-native test filter]:filter:' \
    '(--task --test-task)'{--task,--test-task}'[exclusive validation unit; Gradle task for Android/Gradle or native action/test target elsewhere]:task:' \
    '--failed[rerun tests that failed in the previous structured run]' \
    '--changed[select tests related to changed files]' \
    '--changed-from[Git base for changed test selection]:revision:' \
    '--instrumented[run Android instrumentation tests on a device]' \
    '(--workflow-parallel --no-workflow-parallel)--workflow-parallel[enable safe dependency/scope-aware workflow-node parallelism]' \
    '(--workflow-parallel --no-workflow-parallel)--no-workflow-parallel[force sequential workflow-node execution]' \
    '--workflow-max-workers[maximum concurrent workflow nodes]:count:' \
    '--watch[rerun validation when files change]' \
    '--watch-interval[seconds between watch polls]:seconds:' \
    '--matrix[comma-separated runtime IDs, target names, or variants]:matrix:' \
    '--matrix-mode[matrix interpretation]:mode:(rid target variant)' \
    '--fail-on-conflict[fail when nested overlays write the same path]' \
    '--ignore-checksum[skip SHA-256 checks while retaining other manifest checks]' \
    '--sequential[use a persistent Android/Gradle validation session]' \
    '--resume[resume the active validation session]' \
    '--restart-session[replace an existing non-transactional session]' \
    '--retry-failed[retry the cached failed phase and dependents]' \
    '--next[explicitly run the next pending phase; continue does this by default]' \
    '*--phase[run a named phase plus pending prerequisites; repeat for selected scope]:phase:(static preflight dependencies compile resources unit-test package lint instrumentation finalize)' \
    '--phase-only[run exactly one pending phase]:phase:(static preflight dependencies compile resources unit-test package lint instrumentation finalize)' \
    '--through[run pending phases through this phase]:phase:(static preflight dependencies compile resources unit-test package lint instrumentation finalize)' \
    '--complete[run all remaining phases]' \
    '(--from-phase --from)'{--from-phase,--from}'[start this invocation at a named phase]:phase:(static preflight dependencies compile resources unit-test package lint instrumentation finalize)' \
    '--pause-after[pause after a named phase]:phase:(static preflight dependencies compile resources unit-test package lint instrumentation finalize)' \
    '--max-phases[maximum phases for this invocation]:count:' \
    '--max-runtime[pause at a phase boundary after this duration]:duration:' \
    '--require-complete[return failure when the invocation pauses incomplete]' \
    '--stop-below-battery[pause below this battery percentage]:percent:' \
    '--require-charging[pause unless the Android device is charging]' \
    '--stop-above-temperature[pause above this device temperature]:celsius:' \
    '--stop-below-disk-mb[pause when repository storage falls below this threshold]:mebibytes:' \
    '(--stop-gradle-after-phase --keep-gradle-daemon)--stop-gradle-after-phase[stop Gradle daemons after each phase]' \
    '(--stop-gradle-after-phase --keep-gradle-daemon)--keep-gradle-daemon[keep Gradle daemons between phase invocations]' \
    '--profile[validation workflow profile]:profile:(minimal default android-full release termux architecture-final final-seal)' \
    '--resource-profile[validation resource profile]:profile:(automatic android-low-power standard)' \
    '--max-workers[override validation worker count for this invocation]:count:' \
    '--cpu-limit[override validation CPU limit for this invocation]:count:' \
    '--gradle-heap-mb[override the Gradle validation heap]:mebibytes:' \
    '--gradle-metaspace-mb[override max metaspace; 0 disables Devtool cap]:mebibytes:' \
    '--metaspace-guard-mb[alias for --gradle-metaspace-mb]:mebibytes:' \
    '--no-metaspace-guard[do not set an explicit Gradle MaxMetaspaceSize]' \
    '--memory-guard-mb[override the free-memory guard]:mebibytes:' \
    '(--low-priority --normal-priority)--low-priority[run validation commands at low scheduler priority]' \
    '(--low-priority --normal-priority)--normal-priority[run validation commands at normal scheduler priority]' \
    '(--parallel --no-parallel)--parallel[enable Gradle parallel validation]' \
    '(--parallel --no-parallel)--no-parallel[disable Gradle parallel validation]' \
    '(--build-cache --no-build-cache)--build-cache[enable the Gradle build cache]' \
    '(--build-cache --no-build-cache)--no-build-cache[disable the Gradle build cache]' \
    '(--daemon --no-daemon)--daemon[allow the Gradle daemon during validation]' \
    '(--daemon --no-daemon)--no-daemon[disable the Gradle daemon during validation]' \
    '--max-infrastructure-retries[automatic environment retries per phase]:count:' \
    '--max-resource-retries[automatic resource retries per phase]:count:' \
    '--no-auto-retry[disable automatic environment and resource retries]' \
    '--all[reset all validation phase verdicts]' \
    '--reason[audit reason for accepting plan drift]:reason:' \
    '--rollback-on-failure[roll back an attached apply transaction on phase failure]' \
    '--failure-action[action after sequential validation failure]:action:(pause rollback keep prompt)' \
    '--session[specific validation session ID]:session:' \
    '--scope[stable run scope]:scope:(next through selected remaining)' \
    '--if-revision[require this session revision]:revision:' \
    '--after-phase[pause after the active phase finishes]' \
    '--cancel[cancel a pending pause-after-phase request]' \
    '--rollback[explicitly roll back when aborting]' \
    '--events[include durable events in status JSON]' \
    '--after-event[return events after this event ID]:event-id:' \
    '(--event-limit --limit)'{--event-limit,--limit}'[maximum event records]:count:' \
    '--json[emit the stable validation control envelope]' \
    '--machine[machine response formatting]:format:(pretty compact)' \
    '--keep-changes[keep repository changes when aborting]' \
    '1:validation action or artifact scope:(start next run resume continue retry status checkpoint plan events pause stop finalize abort recover reset accept-plan-change overlay overlays)' \
    '2:artifact:_devtool_artifact_path'
}

_devtool_apply_args() {
  _arguments \
    '--validate[run validation after applying]' \
    '--defer-validate[apply now and create a resumable validation session]' \
    '--static-validate[apply and run only embedded static validation scripts]' \
    '--no-validate[disable validation requested by the artifact manifest]' \
    '--test-profile[saved test profile for artifact validation]:profile:_devtool_test_profiles' \
    '*--select[select tests by type, suite, tag, class, or name]:selector:' \
    '*--pytest-test[run exactly this pytest file or node ID]:pytest node id:_devtool_pytest_tests' \
    '*--pytest-marker[pytest -m marker expression]:marker expression:' \
    '--pytest-k[pytest -k keyword expression]:keyword expression:' \
    '*--pytest-arg[pass one structured argument directly to pytest]:pytest argument:' \
    '--pytest-include-defaults[keep configured or affected default pytest targets with explicit pytest tests]' \
    '*--id[select an exact framework test ID]:test id:' \
    '*--tag[include a test tag or trait]:tag:' \
    '*--exclude-tag[exclude a test tag or trait]:tag:' \
    '*--path[select tests below a source path or package]:path:_files' \
    '*--class[select a test class]:class:' \
    '*--tests[select a test class]:class:' \
    '*--method[select a test method]:method:' \
    '--filter[framework-native test filter]:filter:' \
    '(--task --test-task)'{--task,--test-task}'[exclusive validation unit; Gradle task for Android/Gradle or native action/test target elsewhere]:task:' \
    '--failed[rerun tests that failed in the previous structured run]' \
    '--changed[select tests related to changed files]' \
    '--changed-from[Git base for changed test selection]:revision:' \
    '--instrumented[run Android instrumentation tests on a device]' \
    '--validate-only[skip apply and run validation only]' \
    '--preview[preview archive changes without applying]' \
    '--diff[show a repository-relative unified diff]' \
    '--interactive[show preview and ask before applying]' \
    '--no-manifest-check[disable devtool artifact manifest validation]' \
    '--ignore-checksum[skip SHA-256 checks while retaining other manifest checks]' \
    '--keep-artifact[keep the source artifact after successful apply]' \
    '--delete-artifact-on-failure[delete the source artifact after failed apply]' \
    '--no-rollback-on-failure[disable rollback when apply or validation fails]' \
    '--no-apply-notification[do not show the final apply notification]' \
    '--skip-restore[skip restore during validation]' \
    '--skip-build[skip build during validation]' \
    '--skip-test[skip tests during validation]' \
    '--sequential[keep apply validation as a durable session]' \
    '--resume[resume the active validation session]' \
    '--retry-failed[retry the cached failed validation phase]' \
    '--next[explicitly run the next validation phase; continue does this by default]' \
    '--phase[run a named phase plus pending prerequisites]:phase:(static preflight dependencies compile resources unit-test package lint instrumentation finalize)' \
    '--phase-only[run exactly one pending validation phase]:phase:(static preflight dependencies compile resources unit-test package lint instrumentation finalize)' \
    '--through[run validation through this phase]:phase:(static preflight dependencies compile resources unit-test package lint instrumentation finalize)' \
    '--complete[run all remaining validation phases]' \
    '--pause-after[pause after a named validation phase]:phase:(static preflight dependencies compile resources unit-test package lint instrumentation finalize)' \
    '--max-phases[maximum phases for this invocation]:count:' \
    '--max-runtime[pause at a phase boundary after this duration]:duration:' \
    '--require-complete[fail when validation pauses incomplete]' \
    '--stop-below-battery[pause below this battery percentage]:percent:' \
    '--require-charging[pause unless charging]' \
    '--stop-above-temperature[pause above this temperature]:celsius:' \
    '--stop-below-disk-mb[pause when repository storage falls below this threshold]:mebibytes:' \
    '(--stop-gradle-after-phase --keep-gradle-daemon)--stop-gradle-after-phase[stop Gradle daemons after each phase]' \
    '(--stop-gradle-after-phase --keep-gradle-daemon)--keep-gradle-daemon[keep Gradle daemons between phases]' \
    '--profile[validation workflow profile]:profile:(minimal default android-full release termux architecture-final final-seal)' \
    '--resource-profile[validation resource profile]:profile:(automatic android-low-power standard)' \
    '--max-workers[override validation worker count for this invocation]:count:' \
    '--cpu-limit[override validation CPU limit for this invocation]:count:' \
    '--gradle-heap-mb[override the Gradle validation heap]:mebibytes:' \
    '--gradle-metaspace-mb[override max metaspace; 0 disables Devtool cap]:mebibytes:' \
    '--metaspace-guard-mb[alias for --gradle-metaspace-mb]:mebibytes:' \
    '--no-metaspace-guard[do not set an explicit Gradle MaxMetaspaceSize]' \
    '--memory-guard-mb[override the free-memory guard]:mebibytes:' \
    '(--low-priority --normal-priority)--low-priority[run validation commands at low scheduler priority]' \
    '(--low-priority --normal-priority)--normal-priority[run validation commands at normal scheduler priority]' \
    '(--parallel --no-parallel)--parallel[enable Gradle parallel validation]' \
    '(--parallel --no-parallel)--no-parallel[disable Gradle parallel validation]' \
    '(--build-cache --no-build-cache)--build-cache[enable the Gradle build cache]' \
    '(--build-cache --no-build-cache)--no-build-cache[disable the Gradle build cache]' \
    '(--daemon --no-daemon)--daemon[allow the Gradle daemon during validation]' \
    '(--daemon --no-daemon)--no-daemon[disable the Gradle daemon during validation]' \
    '--max-infrastructure-retries[automatic environment retries per phase]:count:' \
    '--max-resource-retries[automatic resource retries per phase]:count:' \
    '--no-auto-retry[disable automatic environment and resource retries]' \
    '--rollback-on-failure[roll back on validation phase failure]' \
    '--failure-action[action after sequential validation failure]:action:(pause rollback keep prompt)' \
    '--session[specific validation session ID]:session:' \
    '--commit[commit after successful apply and validation]:message:' \
    '--commit-mode[Git result policy]:mode:(new amend fixup none)' \
    '--amend[safely amend HEAD after apply]' \
    '--no-commit[disable a commit requested by the artifact manifest]' \
    '--fixup-target[create a fixup commit targeting revision]:revision:' \
    '--reset-author[reset author identity while amending]' \
    '--allow-merge-amend[allow amending a merge commit]' \
    '--allow-published-rewrite[allow local rewrite of published HEAD; never pushes]' \
    '--isolation[apply isolation mode]:mode:(worktree direct)' \
    '--no-artifact-script[skip embedded artifact validation scripts]' \
    '--rebase[rebase a single overlay inventory against the current repository before apply]' \
    '*:artifact:_devtool_artifact_path'
}

_devtool() {
  local context state line
  typeset -A opt_args

  _arguments -C \
    '(-r --repo)'{-r,--repo}'[repository root]:repo:_devtool_repo_path' \
    '(-c --config)'{-c,--config}'[configuration file]:config:_files' \
    '--target[named target/profile from .devtool.toml]:target:_devtool_targets' \
    '*--group[workspace target group from [workspace.groups]]:group:_devtool_groups' \
    '--all-targets[select every configured workspace target]' \
    '--affected[select dependency-aware targets affected by Git changes]' \
    '--affected-base[Git base revision for affected selection]:revision:' \
    '--affected-head[Git head revision for affected selection]:revision:' \
    '--variant[build/test variant within the selected target]:variant:_devtool_variants' \
    '--solution[exact solution path for restore/build/test]:solution:_files' \
    '--project[exact project path for run/validation]:project:_files' \
    '(--runner --project-type)'{--runner,--project-type}'[override project runner; --project-type is a compatibility alias]:runner:(auto dotnet android gradle python node rust go maven cmake meson make command unknown)' \
    '(-v --verbose)'{-v,--verbose}'[stream full command output]' \
    '(-q --quiet)'{-q,--quiet}'[suppress normal human output]' \
    '--trace[emit architecture event traces and full exception tracebacks]' \
    '(-y --yes)'{-y,--yes}'[assume yes for confirmations]' \
    '(-f --force)'{-f,--force}'[force dangerous actions]' \
    '--allow-pending-validation[allow a new operation while another artifact remains pending validation]' \
    '--dry-run[print commands without executing]' \
    '--no-build[for test, do not rebuild before dotnet test]' \
    '(-k --continue-on-error)'{-k,--continue-on-error}'[continue with the next independent step after an error]' \
    '--fail-fast[stop the batch at the first failed step]' \
    '--performance[write a JSON performance report]' \
    '--performance-file[performance report path]:file:_files' \
    '--machine-file[write the machine envelope]:file:_files' \
    '--json[emit a universal machine-readable JSON invocation envelope]' \
    '(-p --copy)'{-p,--copy}'[copy summary on success or verbose output on failure]' \
    '--no-clipboard[disable clipboard prompts and copy behavior]' \
    '--copy-via[preferred clipboard backend]:backend:(auto socket fifo termux x11 wayland)' \
    '(--output-debug --no-output-debug)--output-debug[show and persist the terminal summary and any clipboard payload]' \
    '(--output-debug --no-output-debug)--no-output-debug[disable terminal and clipboard output debugging even when DEVTOOL_OUTPUT_DEBUG is enabled]' \
    '--theme[summary theme]:theme:(auto plain)' \
    '--ui-profile[shared UI profile]:profile:_devtool_ui_profiles' \
    '--ui-theme[shared UI theme]:theme:(auto light dark)' \
    '--ui-density[shared information density]:density:(dense compact comfortable detailed)' \
    '--ui-icons[semantic icon rendering mode]:icons:(auto nerd unicode ascii native)' \
    '--ui-motion[shared motion preference]:motion:(auto full reduced)' \
    '--ui-verbosity[shared information verbosity]:verbosity:(compact normal detailed debug)' \
    '--log-dir[override command log directory]:directory:_files -/' \
    '--stdout-payload[select stdout content]:payload:(auto summary raw)' \
    '(--auto-hud --hud-auto --no-hud)'{--auto-hud,--hud-auto}'[auto-start or reuse the selected HUD]' \
    '(--auto-hud --hud-auto --no-hud)--no-hud[do not publish live HUD progress]' \
    '--hud-mode[HUD backend]:mode:(desktop android-bridge desktop-window gtk4-window web studio terminal)' \
    '(--hud-overlay --hud-no-overlay)--hud-overlay[also show the run in the Android floating overlay]' \
    '(--hud-overlay --hud-no-overlay)--hud-no-overlay[keep the run in the app without opening the overlay]' \
    '(--host --chroot --ssh --ssh-host --proot-distro --execution-environment)--host[force the current host environment]' \
    '(--host --chroot --ssh --ssh-host --proot-distro --execution-environment)--chroot[run command phases in a named chroot]:name:' \
    '(--host --chroot --ssh --ssh-host --proot-distro --execution-environment)'{--ssh,--ssh-host}'[use an SSH host for devtool or command-phase execution]:host:_hosts' \
    '(--host --chroot --ssh --ssh-host --proot-distro --execution-environment)--proot-distro[run command phases in a PRoot-Distro container]:distro:' \
    '(--host --chroot --ssh --ssh-host --proot-distro --execution-environment)--execution-environment[execution backend]:backend:(auto native-termux chroot proot-distro desktop-linux android-device external-device ssh)' \
    '--ssh-profile[named SSH worker profile]:name:' \
    '--ssh-port[SSH port]:port:' \
    '--ssh-user[SSH login user]:user:' \
    '--ssh-identity[SSH private key]:file:_files' \
    '--ssh-session[tmux session name]:name:' \
    '--ssh-cwd[working directory on the SSH host; no local path inference]:directory:' \
    '--ssh-tool[remote devtool executable used by devtool mode]:command:' \
    '--ssh-mode[SSH execution mode]:mode:(devtool commands)' \
    '--ssh-stage-dir[remote staging root for copied artifacts/helpers]:directory:' \
    '--ssh-no-artifact-copy[disable automatic SSH/SCP artifact transfer]' \
    '--ssh-detached[start or reuse the remote tmux session without attaching; devtool mode]' \
    '--proot-user[PRoot-Distro guest user]:user:' \
    '--proot-cwd[absolute working directory inside the PRoot guest]:directory:' \
    '--proot-repo-mount[absolute guest mount point for the repository]:directory:' \
    '--proot-bind[additional host-to-guest bind]:mapping:' \
    '--proot-env[explicit guest environment NAME=VALUE]:env:' \
    '--proot-no-auto-bind-repo[disable automatic repository bind]' \
    '--proot-shared-home[share host home with PRoot guest]' \
    '--proot-shared-tmp[share Termux tmp with PRoot guest]' \
    '(--proot-minimal)--proot-isolated[use PRoot-Distro isolated mode on Termux]' \
    '(--proot-isolated)--proot-minimal[use PRoot-Distro minimal mode on Termux]' \
    '--proot-stage-dir[host staging directory bound into PRoot guest]:directory:' \
    '--termux-resource-profile[Termux resource profile]:profile:(auto automatic conservative balanced performance off none)' \
    '--no-termux-resource-profile[bypass automatic Termux resource defaults]' \
    '(--no-termux-wake-lock)--termux-wake-lock[acquire Termux wake lock during command phases]' \
    '(--termux-wake-lock)--no-termux-wake-lock[disable Termux wake-lock integration]' \
    '--shared-storage-policy[Android shared-storage build policy]:policy:(allow warn deny)' \
    '--allow-shared-storage-build[allow repository builds from Android shared storage]' \
    '--artifact-export[copy verified artifacts to directory]:directory:' \
    '--no-auto-chroot[disable automatic Termux-to-chroot routing]' \
    '--no-bootstrap-downloads[never download or install missing toolchain components]' \
    '--bootstrap-downloads[override receipt/config provisioning policy]:policy:(auto missing force never)' \
    '--deny-network[deny Devtool-managed bootstrap network downloads]' \
    '--offline[disable bootstrap downloads, deny bootstrap network, and use Gradle offline mode]' \
    '--toolchain-profile[chroot/toolchain profile]:profile:' \
    '--android-sdk-root[Android SDK root]:directory:_files -/' \
    '--android-sdk-provider[Android SDK provider]:provider:(auto termuxvoid homuhomu managed)' \
    '--gradle-command[Gradle or gradlew executable]:command:_command_names -e' \
    '--gradle-provider[Gradle provider]:provider:(auto wrapper termux system download)' \
    '--java-home[JDK home]:directory:_files -/' \
    '--java-provider[Java provider]:provider:(auto termux system)' \
    '--gradle-user-home[Gradle user home]:directory:_files -/' \
    '--native-provider[Native provider]:provider:(auto termux-sysroot ndk system)' \
    '--native-cache[Native compiler cache]:provider:(auto ccache off)' \
    '--android-build-tools-provider[Android native build-tools provider]:provider:(auto termux sdk commit451 none)' \
    '--aapt2-provider[AAPT2 provider]:provider:(auto termux sdk revanced none)' \
    '1:command:->command' \
    '*::argument:->args'

  case $state in
    command)
      _devtool_commands
      ;;
    args)
      case $line[1] in
        git)
          if (( ${#line} < 2 )); then
            _values 'git action' state
          else
            case $line[2] in
              state)
                _arguments '--json[emit machine-readable Git state]'
                ;;
            esac
          fi
          ;;
        wrapper)
          if (( ${#line} < 2 )); then
            _values 'wrapper action' describe help discover resolve exec init update verify status seal
          else
            case $line[2] in
              describe|discover|status|verify|seal)
                _arguments '--json[emit machine-readable JSON]'
                ;;
              init)
                _arguments '--force[replace drifted or unmanaged launcher files]' '--json[emit launcher status as JSON]'
                ;;
              update)
                _arguments '--force[explicitly replace unmanaged launcher files]' '--json[emit launcher status as JSON]'
                ;;
              help)
                if (( ${#line} < 3 )); then
                  _arguments '--json[emit wrapper discovery metadata as JSON]' '1:wrapper command:_devtool_wrapper_commands'
                else
                  _arguments '--json[emit wrapper discovery metadata as JSON]'
                fi
                ;;
              resolve)
                if (( ${#line} < 3 )); then
                  _devtool_wrapper_commands
                else
                  _devtool_wrapper_options "${line[3]}"
                fi
                ;;
              exec)
                if (( ${#line} < 3 )); then
                  _devtool_wrapper_commands
                else
                  _devtool_wrapper_options "${line[3]}"
                fi
                ;;
            esac
          fi
          ;;
        federation|fed)
          if (( ${#line} < 2 )); then
            _values 'federation action' list inspect preflight gate1 gate2 execution-preflight final-seal seal status summary plan run exec lock capabilities graph interfaces conformance interface-check affected why
          else
            case $line[2] in
              list|preflight|gate1|gate2|execution-preflight|final-seal|seal)
                _arguments '--json[emit machine-readable federation state]'
                ;;
              status|summary)
                _arguments '--run[parent or child EXO run ID]:run-id:' '--json[emit machine-readable federation observability]'
                ;;
              capabilities|graph)
                _arguments '--json[emit machine-readable capability dependency graph]'
                ;;
              interfaces)
                _arguments '--json[emit machine-readable federation interface catalog]'
                ;;
              conformance|interface-check)
                _arguments '--interface[check all groups bound to interface]:interface:' '--group[check one group interface contract]:group:_devtool_federation_groups' '--json[emit machine-readable conformance evidence]'
                ;;
              affected)
                _arguments '--since[detect direct changes since revision]:revision:' '*--repository[seed directly changed federation repository]:repository:_devtool_federation_repositories' '--json[emit machine-readable impact evidence]'
                ;;
              why)
                _arguments '1:repository node workflow group or interface:' '--since[detect direct changes since revision]:revision:' '*--repository[seed directly changed federation repository]:repository:_devtool_federation_repositories' '--json[emit machine-readable reason evidence]'
                ;;
              inspect)
                _arguments '1:federated repository:_devtool_federation_repositories' '--json[emit machine-readable repository boundary state]'
                ;;
              plan)
                _arguments '1:federation workflow:_devtool_federation_workflows' '--group[plan a federation group]:group:_devtool_federation_groups' '--freeze[persist frozen multi-repository source lock]' '--drift-policy[frozen source drift policy]:policy:(fail replan allow-if-equivalent)' '--json[emit machine-readable federation state]'
                ;;
              lock)
                _arguments '--update[refresh exactly one repository in persisted lock]:repository:_devtool_federation_repositories' '--json[emit machine-readable lock and verification state]'
                ;;
              run)
                _arguments '1:federation workflow:_devtool_federation_workflows' '--group[execute/plan a federation group]:group:_devtool_federation_groups' '--parallel[run dependency-ready federation children concurrently when safe]' '--max-parallel[maximum concurrent federation children; implies --parallel]:count:' '--affected[run only dependency-aware affected workflow repositories]' '--since[with --affected detect direct changes since revision]:revision:' '*--repository[with --affected seed changed repository]:repository:_devtool_federation_repositories' '--drift-policy[frozen source drift policy]:policy:(fail replan allow-if-equivalent)' '--json[emit machine-readable federation state]'
                ;;
              exec)
                _arguments '1:federated repository:_devtool_federation_repositories' '--plan[resolve without execution]' '--json[emit machine-readable bridge state]' '*:child contract arguments:'
                ;;
            esac
          fi
          ;;
        restore|rs|lint|run|rn|clean|cl|inspect|info|status|st)
          _arguments \
            '--target[named target/profile]:target:_devtool_targets' \
            '*--group[workspace target group]:group:_devtool_groups' \
            '--all-targets[select every workspace target]' \
            '--affected[select dependency-aware affected targets]' \
            '--affected-base[Git base revision for affected selection]:revision:' \
            '--affected-head[Git head revision for affected selection]:revision:' \
            '--variant[target variant]:variant:_devtool_variants'
          ;;
        test|t)
          _devtool_test_args
          ;;
        build|b|package|pkg)
          _arguments \
            '--target[named target/profile]:target:_devtool_targets' \
            '*--group[workspace target group]:group:_devtool_groups' \
            '--all-targets[select every workspace target]' \
            '--affected[select dependency-aware affected targets]' \
            '--affected-base[Git base revision for affected selection]:revision:' \
            '--affected-head[Git head revision for affected selection]:revision:' \
            '--variant[target variant]:variant:_devtool_variants' \
            '(-o --artifact-output-dir --output-dir)'{-o,--artifact-output-dir,--output-dir}'[copy produced artifacts here]:directory:_files -/' \
            '(-n --artifact-output-name --output-name)'{-n,--artifact-output-name,--output-name}'[base filename for copied artifacts]:name:' \
            '--artifact-collision[managed artifact collision policy]:policy:(unique replace fail)' \
            '(--no-verify-artifacts)--verify-artifacts[verify produced artifacts before registration/export]' \
            '(--verify-artifacts)--no-verify-artifacts[collect artifacts without content verification]' \
            '*:runner args:'
          ;;
        validate|v|start-validation|sv|resume-validation|rv)
          _devtool_validation_args
          ;;
        validation-menu|vm)
          _devtool_validation_menu_args
          ;;
        menu)
          if (( ${#line} < 2 )); then
            _values 'menu' validation
          else
            _devtool_validation_menu_args
          fi
          ;;
        validation-status|vs)
          _arguments '--session[specific validation session ID]:session:' '--events[include durable events]' '--after-event[event cursor]:event-id:' '--event-limit[maximum events]:count:' '--json[emit stable JSON]' '--machine[format]:format:(pretty compact)'
          ;;
        validation-plan|vplan)
          _arguments '--session[specific validation session ID]:session:' '--json[emit stable JSON]' '--machine[format]:format:(pretty compact)'
          ;;
        validation-events|vevents)
          _arguments '--session[specific validation session ID]:session:' '--after-event[event cursor]:event-id:' '--limit[maximum events]:count:' '--json[emit stable JSON]' '--machine[format]:format:(pretty compact)'
          ;;
        validation-report|vreport)
          _arguments '--session[specific validation session ID]:session:' '--json[emit stable JSON]' '--machine[format]:format:(pretty compact)'
          ;;
        validation-pause|vpause|validation-stop|vstop)
          _arguments '--session[specific validation session ID]:session:' '--if-revision[require this revision]:revision:' '--json[emit stable JSON]' '--machine[format]:format:(pretty compact)'
          ;;
        validation-finalize|vf|validation-recover|vrec)
          _arguments '--session[specific validation session ID]:session:' '--if-revision[require this revision]:revision:' '--json[emit stable JSON]' '--machine[format]:format:(pretty compact)'
          ;;
        validation-reset|vreset)
          _arguments '--session[specific validation session ID]:session:' '--phase[reset this phase and dependents]:phase:' '(--from-phase --from)'{--from-phase,--from}'[reset from this phase]:phase:' '--all[reset all validation verdicts]' '--if-revision[require this revision]:revision:' '--json[emit stable JSON]' '--machine[format]:format:(pretty compact)'
          ;;
        validation-accept-plan-change|vapc)
          _arguments '--session[specific validation session ID]:session:' '--reason[audit reason for acceptance]:reason:' '--if-revision[require this revision]:revision:' '--json[emit stable JSON]' '--machine[format]:format:(pretty compact)'
          ;;
        validation-abort|va)
          _arguments '--session[specific validation session ID]:session:' '--keep-changes[keep repository changes instead of rolling back]' '--rollback[explicitly roll back]' '--if-revision[require this revision]:revision:' '--json[emit stable JSON]' '--machine[format]:format:(pretty compact)'
          ;;
        validate-all|wine-validate|wv)
          _arguments \
            '--skip-restore[skip dependency restore]' \
            '--skip-build[skip project build]' \
            '--skip-test[skip project tests]' \
            '--test-profile[saved test profile used by validation]:profile:_devtool_test_profiles' \
            '*--select[select tests by type, suite, tag, class, or name]:selector:' \
            '*--pytest-test[run exactly this pytest file or node ID]:pytest node id:_devtool_pytest_tests' \
    '*--pytest-marker[pytest -m marker expression]:marker expression:' \
    '--pytest-k[pytest -k keyword expression]:keyword expression:' \
    '*--pytest-arg[pass one structured argument directly to pytest]:pytest argument:' \
    '--pytest-include-defaults[keep configured or affected default pytest targets with explicit pytest tests]' \
    '*--id[select an exact framework test ID]:test id:' \
            '*--tag[include a test tag or trait]:tag:' \
            '*--exclude-tag[exclude a test tag or trait]:tag:' \
            '*--path[select tests below a source path or package]:path:_files' \
            '*--class[select a test class]:class:' \
            '*--tests[select a test class]:class:' \
            '*--method[select a test method]:method:' \
            '--filter[framework-native test filter]:filter:' \
            '(--task --test-task)'{--task,--test-task}'[exclusive validation unit; Gradle task for Android/Gradle or native action/test target elsewhere]:task:' \
            '--failed[rerun tests that failed in the previous structured run]' \
            '--changed[select tests related to changed files]' \
            '--changed-from[Git base for changed test selection]:revision:' \
            '--instrumented[run Android instrumentation tests on a device]' \
            '--watch[rerun validation when files change]' \
            '--watch-interval[seconds between watch polls]:seconds:' \
            '--matrix[comma-separated runtime IDs, target names, or variants]:matrix:' \
            '--matrix-mode[matrix interpretation]:mode:(rid target variant)' \
            '--max-workers[override validation worker count for this invocation]:count:' \
            '--cpu-limit[override validation CPU limit for this invocation]:count:' \
            '--gradle-heap-mb[override the Gradle validation heap]:mebibytes:' \
            '--gradle-metaspace-mb[override max metaspace; 0 disables Devtool cap]:mebibytes:' \
            '--metaspace-guard-mb[alias for --gradle-metaspace-mb]:mebibytes:' \
            '--no-metaspace-guard[do not set an explicit Gradle MaxMetaspaceSize]' \
            '--memory-guard-mb[override the free-memory guard]:mebibytes:' \
            '(--low-priority --normal-priority)--low-priority[run validation commands at low scheduler priority]' \
            '(--low-priority --normal-priority)--normal-priority[run validation commands at normal scheduler priority]' \
            '(--parallel --no-parallel)--parallel[enable Gradle parallel validation]' \
            '(--parallel --no-parallel)--no-parallel[disable Gradle parallel validation]' \
            '(--build-cache --no-build-cache)--build-cache[enable the Gradle build cache]' \
            '(--build-cache --no-build-cache)--no-build-cache[disable the Gradle build cache]' \
            '(--daemon --no-daemon)--daemon[allow the Gradle daemon during validation]' \
            '(--daemon --no-daemon)--no-daemon[disable the Gradle daemon during validation]'
          ;;
        doctor|dr)
          _arguments \
            '--target[named target/profile]:target:_devtool_targets' \
            '*--group[workspace target group]:group:_devtool_groups' \
            '--all-targets[select every workspace target]' \
            '--affected[limit workflow preflight to affected targets]' \
            '--affected-base[Git base revision for affected selection]:revision:' \
            '--affected-head[Git head revision for affected selection]:revision:' \
            '--workflow[workflow/action to analyze]:workflow:' \
            '--fix[create safe devtool state and cache directories]' \
            '--explain[explain Termux provider selection and rejection reasons]' \
            '--json[emit structured runtime, device, project, and workflow preflight health checks]'
          ;;
        ssh)
          if (( ${#line} < 2 )); then
            _values 'ssh worker action' profiles list add remove rm default qualify seal doctor bootstrap sessions ls workspace ws sync retrieve return runs resume cancel recover
          else
            case ${line[2]} in
              profiles|list)
                _arguments '--format[output format]:format:(text json)'
                ;;
              add)
                _arguments \
                  '1:worker profile:_devtool_ssh_profiles' \
                  '--host[SSH host name or address]:host:' \
                  '--port[SSH port]:port:' \
                  '--user[remote SSH user]:user:' \
                  '--identity[local SSH identity file]:file:_files' \
                  '--cwd[existing remote checkout path]:path:' \
                  '--workspace-mode[remote workspace mode]:mode:(managed existing ephemeral)' \
                  '--workspace-root[managed remote workspace root]:path:' \
                  '--bootstrap-policy[remote bootstrap policy]:policy:(missing never)' \
                  '--execution-provider[worker execution provider]:provider:(native ssh-native proot-oci ssh-proot-oci)' \
                  '--host-key-policy[OpenSSH host key policy]:policy:(accept-new strict system)' \
                  '--min-free-mb[minimum remote free storage]:mebibytes:' \
                  '--no-default[do not make this the default SSH worker]' \
                  '--force[replace an existing profile in the selected scope]' \
                  '(--repo-local)--global[manage the reusable user-global profile]' \
                  '(--global)--repo-local[manage only this repository profile]' \
                  '--format[output format]:format:(text json)'
                ;;
              remove|rm|default)
                _arguments '1:worker profile:_devtool_ssh_profiles' '(--repo-local)--global[manage the reusable user-global profile]' '(--global)--repo-local[manage only this repository profile]' '--format[output format]:format:(text json)'
                ;;
              qualify|seal)
                _arguments '1:worker profile:_devtool_ssh_profiles' '--format[output format]:format:(text json)' '--dry-run[plan qualification without modifying the worker]' '--no-bootstrap[do not install missing worker tools]' '--no-sync[skip repository synchronization checks]' '--project-validation[run the real managed SSH validation path]' '--target[target/profile for project validation]:target:_devtool_targets' '*--task[project validation task; repeatable]:task:' '--single-validation[run only one project validation pass]'
                ;;
              doctor|sessions|ls|workspace|ws|runs)
                _arguments '1:worker profile:_devtool_ssh_profiles' '--format[output format]:format:(text json)'
                ;;
              bootstrap)
                _arguments '1:worker profile:_devtool_ssh_profiles' '--format[output format]:format:(text json)' '--dry-run[show package installation plan only]' '--force[override bootstrap.policy=never]'
                ;;
              sync)
                _arguments '1:worker profile:_devtool_ssh_profiles' '--format[output format]:format:(text json)' '--dry-run[show exact repository delta without modifying worker]'
                ;;
              retrieve|return)
                _arguments '1:worker profile:_devtool_ssh_profiles' '--format[output format]:format:(text json)' '--run-id[select one Devtool run]:run-id:' '--session[select one SSH phase session]:session:'
                ;;
              resume|cancel)
                _arguments '1:worker profile:_devtool_ssh_profiles' '--format[output format]:format:(text json)' '--run-id[select one Devtool run]:run-id:' '--session[select one SSH phase session]:session:' '--node-id[select one canonical provider node]:node-id:'
                ;;
              recover)
                _arguments '1:worker profile:_devtool_ssh_profiles' '--format[output format]:format:(text json)' '--run-id[restrict stale-run recovery to one Devtool run]:run-id:' '--node-id[restrict recovery to one canonical provider node]:node-id:'
                ;;
            esac
          fi
          ;;
        oci)
          if (( ${#line} < 2 )); then
            _values 'OCI action' definition build gate1 workspace validate-image image-status status update prune diagnostics diag final-seal seal doctor images containers list bootstrap install remove rm
          else
            case ${line[2]} in
              definition)
                _arguments '--format[output format]:format:(text json)'
                ;;
              final-seal|seal)
                _arguments '--format[output format]:format:(text json)' '--strict[fail when final seal checks fail]'
                ;;
              build)
                _arguments '1:worker profile:_devtool_ssh_profiles' '--tag[OCI image tag/reference]:tag:' '--install-as[install built image as this container]:container:' '--no-cache[disable PRoot-Distro build cache]' '--dry-run[plan without mutating worker]' '--format[output format]:format:(text json)'
                ;;
              gate1)
                _arguments '1:worker profile:_devtool_ssh_profiles' '--container[installed container name]:container:' '--tag[OCI image tag/reference]:tag:' '--format[output format]:format:(text json)'
                ;;
              workspace)
                _arguments '1:worker profile:_devtool_ssh_profiles' '--dry-run[validate workspace/cache paths without creating caches]' '--format[output format]:format:(text json)'
                ;;
              image-status|status|diagnostics|diag|doctor|images|containers|list)
                _arguments '1:worker profile:_devtool_ssh_profiles' '--format[output format]:format:(text json)'
                ;;
              validate-image)
                _arguments '1:worker profile:_devtool_ssh_profiles' '2:installed container:container:' '--format[output format]:format:(text json)'
                ;;
              update)
                _arguments '1:worker profile:_devtool_ssh_profiles' '--apply[execute the image refresh]' '--force[rebuild/reinstall even when cached]' '--no-cache[disable build cache]' '--format[output format]:format:(text json)'
                ;;
              prune)
                _arguments '1:worker profile:_devtool_ssh_profiles' '--apply[remove non-canonical cached images]' '--format[output format]:format:(text json)'
                ;;
              bootstrap)
                _arguments '1:worker profile:_devtool_ssh_profiles' '--dry-run[plan without mutating worker]' '--force[force bootstrap/repair]' '--format[output format]:format:(text json)'
                ;;
              install)
                _arguments '1:worker profile:_devtool_ssh_profiles' '2:OCI registry reference or archive:_files' '--name[local container name]:container:' '--architecture[target architecture]:architecture:' '--dry-run[plan without mutating worker]' '--format[output format]:format:(text json)'
                ;;
              remove|rm)
                _arguments '1:worker profile:_devtool_ssh_profiles' '2:container or cached image:target:' '--image[remove a cached image instead of a container]' '--dry-run[plan without mutating worker]' '--format[output format]:format:(text json)'
                ;;
            esac
          fi
          ;;
        bootstrap)
          if (( ${#line} < 2 )); then
            _values 'bootstrap action' install status remove
          else
            case ${line[2]} in
              install)
                _arguments \
                  '--destination[target destination]:destination:(current termux linux-arm64 linux-amd64 chroot\:androidos chroot-arm64\:androidos chroot-amd64\:androidos)' \
                  '--format[output format]:format:(text json)' \
                  '--java-version[JDK major version]:version:' \
                  '--gradle-version[managed Gradle version]:version:' \
                  '--android-platform[Android platform]:platform:' \
                  '--build-tools[Android Build Tools version]:version:' \
                  '--ndk-version[Android NDK version]:version:' \
                  '--cmake-version[CMake version]:version:' \
                  '--sdk-provider[Android SDK provider]:provider:(auto termuxvoid homuhomu managed)' \
                  '--aapt2-provider[AAPT2 provider]:provider:(auto termux sdk revanced none)' \
                  '--arm-build-tools-provider[ARM build-tools provider]:provider:(auto termux sdk commit451 none)' \
                  '--native-provider[native compiler provider]:provider:(auto termux-sysroot ndk system)' \
                  '--gradle-provider[Gradle provider]:provider:(auto wrapper termux system download)' \
                  '--java-provider[Java provider]:provider:(auto termux system)' \
                  '--sdk-root[explicit Android SDK root]:directory:_files -/' \
                  '--gradle-user-home[Gradle user home]:directory:_files -/' \
                  '--cache-dir[Android bootstrap cache directory]:directory:_files -/' \
                  '(--upgrade)--repair[repair missing or corrupt components]' \
                  '(--repair)--upgrade[force refresh Devtool-managed toolchains]' \
                  '--dry-run[probe provisioning without writing a receipt]'
                ;;
              status|remove)
                _arguments \
                  '--destination[target destination]:destination:(current termux linux-arm64 linux-amd64 chroot\:androidos chroot-arm64\:androidos chroot-amd64\:androidos)' \
                  '--format[output format]:format:(text json)'
                ;;
            esac
          fi
          ;;
        android)
          if (( ${#line} < 2 )); then
            _values 'android action' capabilities devices doctor signing install uninstall launch stop restart clear-data shell push pull logcat screenshot permission instrumentation smoke release
          else
            case ${line[2]} in
              capabilities|doctor)
                _arguments \
                  '--provider[ADB provider override]:provider:(auto sdk termux termux-adb path none)' \
                  '--adb-path[explicit ADB executable path]:executable:_files' \
                  '--format[output format]:format:(text json)'
                ;;
              devices)
                _arguments \
                  '1:device action:(list doctor)' \
                  '--provider[ADB provider override]:provider:(auto sdk termux termux-adb path none)' \
                  '--adb-path[explicit ADB executable path]:executable:_files' \
                  '--format[output format]:format:(text json)' \
                  '--enrich[query API ABI battery screen and boot metadata]' \
                  '--policy[deterministic device selection policy]:policy:(exactly-one serial preferred first-physical first-emulator all)' \
                  '--serial[device serial or transport ID]:serial:' \
                  '--preferred[preferred serial model or device]:device:' \
                  '--include-unusable[allow offline or unauthorized devices in explicit selection]'
                ;;
              signing)
                if (( ${#line} < 3 )); then
                  _values 'signing action' create list inspect verify compare import export-certificate
                else
                  case ${line[3]} in
                    create)
                      _arguments '--keystore[keystore path]:file:_files' '--alias[key alias]:alias:' '--store-password-env[store password environment variable]:name:' '--key-password-env[key password environment variable]:name:' '--dname[certificate distinguished name]:dn:' '--overwrite[replace an existing keystore]' '--format[output format]:format:(text json)'
                      ;;
                    list)
                      _arguments '--keystore[keystore path]:file:_files' '--store-password-env[store password environment variable]:name:' '--format[output format]:format:(text json)'
                      ;;
                    inspect)
                      _arguments '--keystore[keystore path]:file:_files' '--alias[key alias]:alias:' '--store-password-env[store password environment variable]:name:' '--format[output format]:format:(text json)'
                      ;;
                    verify)
                      _arguments '--artifact[APK/AAB/JAR to verify]:file:_files' '--keystore[keystore path]:file:_files' '--alias[key alias]:alias:' '--store-password-env[store password environment variable]:name:' '--format[output format]:format:(text json)'
                      ;;
                    compare)
                      _arguments '1:first signed artifact:_files' '2:second signed artifact:_files' '--format[output format]:format:(text json)'
                      ;;
                    import)
                      _arguments '--source[source JKS/keystore]:file:_files' '--destination[destination PKCS12/keystore]:file:_files' '--alias[optional alias]:alias:' '--source-password-env[source password environment variable]:name:' '--destination-password-env[destination password environment variable]:name:' '--overwrite[replace destination]' '--format[output format]:format:(text json)'
                      ;;
                    export-certificate)
                      _arguments '--destination[PEM certificate destination]:file:_files' '--keystore[keystore path]:file:_files' '--alias[key alias]:alias:' '--store-password-env[store password environment variable]:name:' '--format[output format]:format:(text json)'
                      ;;
                  esac
                fi
                ;;
              install)
                _arguments '1:APK:_files' '--serial[device serial]:serial:' '--policy[device policy]:policy:(exactly-one serial preferred first-physical first-emulator)' '--no-replace[install without -r]' '--downgrade[allow version downgrade]'
                ;;
              uninstall)
                _arguments '1:package:package:' '--serial[device serial]:serial:' '--policy[device policy]:policy:(exactly-one serial preferred first-physical first-emulator)' '--keep-data[preserve package data/cache]'
                ;;
              launch|restart)
                _arguments '1:package:package:' '--activity[activity/component]:activity:' '--serial[device serial]:serial:' '--policy[device policy]:policy:(exactly-one serial preferred first-physical first-emulator)'
                ;;
              stop|clear-data)
                _arguments '1:package:package:' '--serial[device serial]:serial:' '--policy[device policy]:policy:(exactly-one serial preferred first-physical first-emulator)'
                ;;
              shell)
                _arguments '--serial[device serial]:serial:' '--policy[device policy]:policy:(exactly-one serial preferred first-physical first-emulator)' '*:adb shell argument:'
                ;;
              push|pull)
                _arguments '1:source:_files' '2:destination:' '--serial[device serial]:serial:' '--policy[device policy]:policy:(exactly-one serial preferred first-physical first-emulator)'
                ;;
              logcat)
                _arguments '--lines[number of recent logcat lines]:count:' '--serial[device serial]:serial:' '--policy[device policy]:policy:(exactly-one serial preferred first-physical first-emulator)'
                ;;
              screenshot)
                _arguments '1:PNG destination:_files' '--serial[device serial]:serial:' '--policy[device policy]:policy:(exactly-one serial preferred first-physical first-emulator)'
                ;;
              permission)
                _arguments '1:permission action:(grant revoke)' '2:package:package:' '3:permission:permission:' '--serial[device serial]:serial:' '--policy[device policy]:policy:(exactly-one serial preferred first-physical first-emulator)'
                ;;
              release)
                _arguments '--device-tests[release device-test policy]:policy:(disabled when-device-present required)' '--previous-apk[previous APK for upgrade smoke]:file:_files' '--package[application package]:package:' '--activity[launch activity]:activity:' '--evidence-output[immutable release-evidence JSON]:file:_files' '--no-unit-tests[skip unit-test gate]' '--no-lint[skip lint gate]' '--no-install-smoke[skip install smoke]' '--no-upgrade-smoke[skip upgrade smoke]' '--no-verify-signature[skip signature verification]' '--no-verify-certificate-identity[skip expected signer comparison]' '--format[output format]:format:(text json)'
                ;;
              instrumentation)
                _arguments '--class[test class]:class:' '--method[test method]:method:' '--annotation[required annotation]:annotation:' '--not-annotation[excluded annotation]:annotation:' '--size[test size]:size:(small medium large)' '--runner-arg[instrumentation runner KEY=VALUE]:argument:' '--skip-build[reuse existing app/test APKs]' '--format[output format]:format:(text json)'
                ;;
              smoke)
                _arguments '1:smoke gate:(install upgrade cold-start restart restore-restart process-death signed-artifact)' '--apk[APK/artifact path]:file:_files' '--old-apk[previous APK for upgrade smoke]:file:_files' '--package[application package]:package:' '--activity[activity/component]:activity:' '--expected-cert-sha256[expected signing certificate SHA-256]:digest:' '--serial[device serial]:serial:' '--policy[device policy]:policy:(exactly-one serial preferred first-physical first-emulator)'
                ;;
            esac
          fi
          ;;
        termux)
          if (( ${#line} < 2 )); then
            _values 'termux action' doctor packages explain
          else
            case ${line[2]} in
              doctor)
                _arguments '--distro[probe an installed PRoot-Distro guest]:distro:' '--explain[include provider selection and rejection reasons]' '--format[output format]:format:(text json)'
                ;;
              packages)
                _arguments '--format[output format]:format:(text json)'
                ;;
              explain)
                _arguments '--format[output format]:format:(text json)'
                ;;
            esac
          fi
          ;;
        release|rel)
          _arguments '--device-tests[release device-test policy]:policy:(disabled when-device-present required)' '--previous-apk[previous APK for upgrade smoke]:file:_files' '--package[application package]:package:' '--activity[launch activity]:activity:' '--evidence-output[immutable release-evidence JSON]:file:_files' '--no-unit-tests[skip unit-test gate]' '--no-lint[skip lint gate]' '--no-install-smoke[skip install smoke]' '--no-upgrade-smoke[skip upgrade smoke]' '--no-verify-signature[skip signature verification]' '--no-verify-certificate-identity[skip expected signer comparison]' '--format[output format]:format:(text json)'
          ;;
        clean|cl)
          _arguments \
            '--caches[also prune repository-scoped devtool-managed caches and transient state]' \
            '--space[prune safe global Android/toolchain caches managed by Devtool]' \
            '--gradle-user-cache[with --space, also prune old version-specific Gradle caches and build-cache-1]' \
            '--space-target-free-mb[stop space cleanup after reaching this much free space]:MiB:' \
            '--managed-gradle-retention[keep this many newest Devtool-managed Gradle distributions]:count:'
          ;;
        apply-overlay|ao|apply-overlays|aos|apply-snapshot|as)
          _devtool_apply_args
          ;;
        apply)
          if (( ${#line} < 2 )); then
            _values 'apply action' overlay overlays snapshot
          else
            _devtool_apply_args
          fi
          ;;
        validate-overlay|vo)
          _arguments \
            '--fail-on-conflict[for bundles, fail when nested overlays write the same path]' \
            '--ignore-checksum[skip SHA-256 checks while retaining other manifest checks]' \
            '1:artifact:_devtool_artifact_path'
          ;;
        validate-overlays|vos)
          _arguments \
            '--fail-on-conflict[fail when nested overlays write the same path]' \
            '--ignore-checksum[skip SHA-256 checks while retaining other manifest checks]' \
            '*:artifact:_devtool_artifact_path'
          ;;
        overlay)
          if (( ${#line} < 2 )); then
            _values 'overlay action' check status rebase
          else
            case ${line[2]} in
              check)
                _arguments '--diff[include a repository-relative unified diff]' '--diff-context[number of context lines]:lines:' '--max-diff-lines[maximum emitted diff lines]:lines:' '--ignore-checksum[skip checksum verification]' '--json[emit structured overlay preflight JSON]' '1:artifact:_devtool_artifact_path'
                ;;
              rebase)
                _arguments '-o[output rebased archive]:artifact:_files' '--output[output rebased archive]:artifact:_files' '--json[emit structured rebase JSON]' '1:artifact:_devtool_artifact_path'
                ;;
            esac
          fi
          ;;
        plugin|plugins)
          if (( ${#line} < 2 )); then
            _values 'plugin action' list ls doctor
          else
            case ${line[2]} in
              list|ls)
                _arguments '--json[emit plugin discovery JSON]'
                ;;
              doctor)
                _arguments '--json[emit plugin doctor JSON]' '1:plugin:(android python rust magisk)'
                ;;
            esac
          fi
          ;;
        inspect-artifact|ia)
          _arguments \
            '--diff[include a repository-relative unified diff]' \
            '--diff-context[number of context lines]:lines:' \
            '--max-diff-lines[maximum emitted diff lines]:lines:' \
            '1:artifact:_devtool_artifact_path'
          ;;
        artifact)
          if (( ${#line} < 2 )); then
            _values 'artifact action' list ls inspect show verify prune
          else
            case ${line[2]} in
              list|ls)
                _arguments '--json[emit registry JSON]'
                ;;
              inspect|show)
                _arguments '--json[emit artifact JSON]' '1:artifact id, path, or filename:'
                ;;
              verify)
                _arguments '--json[emit verification JSON]' '1:artifact id, path, or filename:'
                ;;
              prune)
                _arguments '--older-than[age threshold such as 30d or 12h]:age:' '--delete-files[delete matching files inside .devtool/build-artifacts]' '--json[emit prune result JSON]'
                ;;
            esac
          fi
          ;;
        tui)
          _arguments '--page[initial page]:page:(dashboard build overlays workers)' '--artifact[overlay artifact to preflight]:artifact:_devtool_artifact_path' '--once[render once and exit]' '--width[override terminal width]:columns:'
          ;;
        cleanup)
          _arguments '--dry-run[show changes without moving or deleting files]' '--prune-history-days[delete archived backup batches older than this many days]:days:'
          ;;
        bundle)
          if (( ${#line} < 2 )); then
            _values 'bundle action' overlays
          else
            _arguments \
              '--name[bundle name]:name:' \
              '--description[bundle description]:description:' \
              '--targets[comma-separated per-overlay targets]:targets:' \
              '--repository-name[repository identity]:name:' \
              '--repository-remote[expected Git remote]:remote:' \
              '--repository-branch[expected Git branch]:branch:' \
              '--commit-message[embedded final commit message]:message:' \
              '--validate[require validation when applied]' \
              '--validation-strategy[validation strategy]:strategy:(per-overlay after-all both)' \
              '--validation-script[embed a disposable validation script]:file:_files' \
              '--validation-runner[script runner]:runner:(bash sh python3)' \
              '--validation-timeout[script timeout in seconds]:seconds:' \
              '--validation-task[Gradle task to store in bundle validation.tasks]:task:' \
              '--validation-tasks[comma-separated Gradle tasks for bundle validation.tasks]:tasks:' \
              '(-o --out)'{-o,--out}'[output bundle archive]:file:_files' \
              '*:overlay:_devtool_artifact_path'
          fi
          ;;
        batch)
          _devtool_steps
          ;;
        ai-prompt|ap)
          _arguments \
            '--repo-name[human-friendly repository name]:name:' \
            '(--runner --project-type)'{--runner,--project-type}'[project runner; --project-type is a compatibility alias]:runner:(auto dotnet android gradle python node rust go maven cmake meson make command unknown)' \
            '--build-target[build target]:path:_files' \
            '--run-target[run target]:path:_files' \
            '--target-name[named target/profile]:target:_devtool_targets' \
            '--solution-target[solution target]:solution:_files' \
            '--delivery-mode[artifact delivery mode]:mode:(overlay snapshot)' \
            '--artifact-type[artifact archive type]:type:(zip tar tar.gz tgz tar.xz txz tar.bz2 tbz2 tar.zst tzst)' \
            '--overlay-count[successful overlay count]:count:' \
            '--commit-message[default commit message]:message:' \
            '--wine[prefer wine-validate wording]' \
            '(-o --output)'{-o,--output}'[write prompt to file]:file:_files' \
            '--format[output format]:format:(markdown text)'
          ;;
        commit|ci)
          _arguments '1:commit message:'
          ;;
        init-config|init)
          _arguments '--force[overwrite an existing configuration]' '1:repository:_devtool_repo_path'
          ;;
        logs)
          _arguments \
            '--print[print the latest log]' \
            '--limit[number of logs to show]:count:' \
            '--pager[pager]:pager:(auto bat cat)' \
            '--lines[lines to show before following]:count:' \
            '--no-follow[print selected active log tail and exit]' \
            '--fzf[use fzf selection]' \
            '--no-fzf[disable fzf selection]' \
            '1:logs command:(latest list ls open show tail follow)' \
            '*:log path:_files'
          ;;
        tag)
          _arguments '(-m --message)'{-m,--message}'[annotated tag message]:message:' '1:tag name:'
          ;;
        release-notes)
          _arguments '--from-ref[starting Git ref]:revision:' '--to-ref[ending Git ref]:revision:'
          ;;
        completion)
          _values 'shell' zsh
          ;;
        completion-state)
          _arguments '--kind[completion value category]:kind:(targets groups ui-profiles test-profiles variants jobs target-jobs target-job-keys target-job-refs workflows wrapper-commands wrapper-aliases wrapper-options pytest-tests all)' '--format[output format]:format:(lines json)'
          ;;
        pytest)
          if (( ${#line} < 2 )); then
            _values 'pytest action' profile
          else
            case $line[2] in
              profile)
                _arguments '--threshold[slow-test threshold in seconds]:seconds:' '--top[number of slow entries to show]:count:' '--strict[fail unmarked slow tests]' '--json[emit structured pytest profile JSON]' '*:pytest arg:_devtool_pytest_tests'
                ;;
            esac
          fi
          ;;
        compatibility|compat)
          _arguments '--json[emit versioned compatibility report]' '--active-only[show only compatibility entries active in selected config]' '--category[filter compatibility category]:category:(config cli environment python-api machine ipc tasker android artifact validation state workflow)' '--strict[fail selected config still needs migration]' '--global[audit user global config]'
          ;;
        final-seal|seal)
          _arguments '--json[emit versioned FS03 final seal report]' '--strict[fail when any final-seal check fails]'
          ;;
        config)
          if (( ${#line} < 2 )); then
            _values 'config action' path show defaults init edit resolve explain migrate doctor
          else
            case $line[2] in
              path|show|defaults|doctor)
                _arguments '--global[use the user global config]' '--minimal[prefer sparse minimal defaults]'
                ;;
              resolve)
                _arguments '--provenance[include provenance explanation]'
                ;;
              explain)
                _arguments '--json[emit machine-readable configuration explanation]'
                ;;
              migrate)
                _arguments '(--check --write)--check[report migration opportunities without changes]' '(--check --write)--write[rewrite safe legacy aliases and create a backup]' '--json[emit machine-readable migration report]' '--global[migrate legacy user UI preferences]'
                ;;
              init)
                _arguments '--global[create the user global config]' '--minimal[write sparse global preferences]' '--force[overwrite existing config]'
                ;;
              edit)
                _arguments '--global[edit the user global config]' '--editor[editor command, defaulting to micro]:editor:'
                ;;
              *)
                ;;
            esac
          fi
          ;;
        detect)
          _arguments '--format[output format]:format:(text json)'
          ;;
        targets|tg|groups|gr|workflows|wf)
          _arguments '--json[emit structured configuration metadata]'
          ;;
        jobs|jb|target-local-jobs|tlj)
          _arguments '--json[emit structured configuration metadata]' '--target[limit to one target]:target:_devtool_targets'
          ;;
        plan|pl)
          _arguments \
            '--target[named target/profile]:target:_devtool_targets' \
            '*--group[workspace target group]:group:_devtool_groups' \
            '--all-targets[select every workspace target]' \
            '--affected[limit planning to dependency-aware affected targets]' \
            '--affected-base[Git base revision for affected selection]:revision:' \
            '--affected-head[Git head revision for affected selection]:revision:' \
            '--variant[target variant]:variant:_devtool_variants' \
            '(--workflow-parallel --no-workflow-parallel)--workflow-parallel[plan safe workflow-node parallelism]' \
            '(--workflow-parallel --no-workflow-parallel)--no-workflow-parallel[plan sequential workflow-node execution]' \
            '--workflow-max-workers[maximum concurrent workflow nodes]:count:' \
            '--json[emit complete graph and scheduling plan as JSON]' \
            '--dot[emit Graphviz DOT]' \
            '--explain[show read/write scopes and serialization reasons]' \
            '--show-commands[show resolved commands and working directories]' \
            '1:workflow action:_devtool_workflow_actions'
          ;;
        invoke)
          _arguments \
            '--target[named target/profile]:target:_devtool_targets' \
            '*--group[workspace target group]:group:_devtool_groups' \
            '--all-targets[select every workspace target]' \
            '--affected[limit invocation to dependency-aware affected targets]' \
            '--affected-base[Git base revision for affected selection]:revision:' \
            '--affected-head[Git head revision for affected selection]:revision:' \
            '--variant[target variant]:variant:_devtool_variants' \
            '(--workflow-parallel --no-workflow-parallel)--workflow-parallel[run independent workflow nodes concurrently when safe]' \
            '(--workflow-parallel --no-workflow-parallel)--no-workflow-parallel[force sequential workflow-node execution]' \
            '--workflow-max-workers[maximum concurrent workflow nodes]:count:' \
            '1:workflow action:_devtool_workflow_actions'
          ;;
        affected|aff)
          _arguments \
            '--target[named target/profile]:target:_devtool_targets' \
            '*--group[candidate workspace target group]:group:_devtool_groups' \
            '--all-targets[consider every workspace target]' \
            '--variant[target variant]:variant:_devtool_variants' \
            '--base[Git base revision]:revision:' \
            '--head[Git head revision used with --base]:revision:' \
            '--explain[explain why each target was selected]' \
            '--json[emit structured affected-target metadata]'
          ;;
        preflight|pf)
          _arguments \
            '--target[named target/profile]:target:_devtool_targets' \
            '*--group[workspace target group]:group:_devtool_groups' \
            '--all-targets[select every workspace target]' \
            '--affected[limit preflight to dependency-aware affected targets]' \
            '--affected-base[Git base revision for affected selection]:revision:' \
            '--affected-head[Git head revision for affected selection]:revision:' \
            '--variant[target variant]:variant:_devtool_variants' \
            '--workflow[workflow/action to inspect]:workflow:' \
            '--json[emit structured graph-aware preflight metadata]'
          ;;
        ui-contract|uic)
          _arguments \
            '--surface[frontend surface to inspect]:surface:(machine desktop tui android hud)' \
            '--width[viewport width/terminal columns for responsive inspection]:width:' \
            '--height[viewport height/terminal rows for responsive inspection]:height:' \
            '*--require-capability[negotiate a required UI capability]:capability:(interactive rich_text tables tree_view notifications persistent_navigation dialogs hyperlinks keyboard touch mouse clipboard native_icons resize_events multi_pane)' \
            '--json[emit preferences, capabilities, responsive policy, progress/diagnostic schemas, tokens, actions, and navigation as JSON]'
          ;;
        handoff)
          _arguments '(-o --output)'{-o,--output}'[write handoff to file]:file:_files'
          ;;
        hud)
          _arguments \
            '--state-dir[HUD state directory]:directory:_files -/' \
            '--mode[HUD renderer]:mode:(auto android-gui android-bridge termux-gui terminal web studio desktop desktop-window gtk4-window desktop-notify v3 v4 v5 v6 console dashboard all unix gnome)' \
            '--host[host or IP for web/studio modes]:host:' \
            '--port[port for web/studio modes]:port:' \
            '--poll-interval[seconds between refreshes]:seconds:' \
            '--recent-events[number of recent events]:count:' \
            '--once[render or update once and exit]' \
            '--follow-active[follow the global active HUD directory]' \
            '(--overlay --no-overlay)--overlay[also publish to the Android floating overlay]' \
            '(--overlay --no-overlay)--no-overlay[do not open the floating overlay]'
          ;;
        android-hud-bridge|termux-hud-bridge)
          _arguments \
            '--state-dir[shared Android bridge state directory]:directory:_files -/' \
            '--poll-interval[seconds between refreshes]:seconds:' \
            '--recent-events[number of recent events]:count:' \
            '--once[render or update once and exit]' \
            '(--follow-active --no-follow-active)--follow-active[follow the active HUD pointer]' \
            '(--follow-active --no-follow-active)--no-follow-active[read the shared bridge directory directly]' \
            '(--overlay --no-overlay)--overlay[also publish to the Android floating overlay]' \
            '(--overlay --no-overlay)--no-overlay[do not open the floating overlay]'
          ;;
        history)
          _arguments '--limit[maximum number of entries]:count:'
          ;;
        rollback)
          _arguments '--last[rollback the last recorded commit]' '--transaction[resume rollback of a specific apply transaction]:transaction:' '--list-transactions[list active apply transactions and recovery state]' '1:commit:'
          ;;
        control-center|gui)
          _arguments \
            '--backend[Control Center backend]:backend:(auto gtk4)' \
            '--target[named target/profile from .devtool.toml]:target:' \
            '--variant[build/test variant within the selected target]:variant:' \
            '--state-dir[directory containing live HUD state]:directory:_files -/' \
            '--follow-active[follow the global active HUD directory]' \
            '--history-limit[artifact history rows to load]:count:' \
            '--recent-events[recent HUD events to load]:count:' \
            '--poll-interval[seconds between GTK refreshes]:seconds:' \
            '--page[initial GTK4 shell page]:page:(overview runs history artifacts diagnostics repositories targets run settings)' \
            '--once[print a GTK4 Control Center JSON snapshot and exit]' \
            '--startup-test[open one GTK frame, record startup diagnostics, then exit]'
          ;;
        ipc-server)
          _arguments \
            '--socket[override the Unix socket path]:socket:_files' \
            '--state-dir[load initial snapshots from a state directory]:directory:_files -/' \
            '--foreground[run the broker in the foreground]'
          ;;
        *)
          ;;
      esac
      ;;
  esac
}

_devtool "$@"
'''
