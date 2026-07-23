from __future__ import annotations

ZSH_COMPLETION = r'''#compdef devtool

# Devtool zsh completion. Keep this file synchronized with devtool/completion.py.

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

_devtool_targets() {
  local repo config
  local -a targets
  repo=${opt_args[--repo]:-${opt_args[-r]:-.}}
  config=${opt_args[--config]:-${opt_args[-c]:-$repo/.devtool.toml}}
  if [[ -r $config ]]; then
    targets=(${(f)"$(sed -nE 's/^\[targets\.([^].]+)\].*/\1/p' "$config" 2>/dev/null)"})
  fi
  if (( ${#targets} )); then
    _describe -t targets 'target' targets
  else
    _message 'target name from .devtool.toml'
  fi
}

_devtool_test_profiles() {
  local repo config
  local -a profiles
  repo=${opt_args[--repo]:-${opt_args[-r]:-.}}
  config=${opt_args[--config]:-${opt_args[-c]:-$repo/.devtool.toml}}
  if [[ -r $config ]]; then
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
  if [[ -r $config ]]; then
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

_devtool_steps() {
  local -a steps
  steps=(
    'restore:restore dependencies'
    'build:build project'
    'test:run tests'
    'lint:run lint and static analysis'
    'validate:run the target validation pipeline'
    'wine-validate:validate with Wine environment settings'
    'run:run project'
    'package:package project'
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
    'start-validation:start a persistent sequential validation session'
    'sv:alias for start-validation'
    'resume-validation:resume a persistent sequential validation session'
    'rv:alias for resume-validation'
    'validation-status:show the active validation session'
    'vs:alias for validation-status'
    'validation-finalize:finalize a completed validation session'
    'vf:alias for validation-finalize'
    'validation-abort:abort and optionally roll back a validation session'
    'va:alias for validation-abort'
    'wine-validate:run validation with Wine environment settings'
    'wv:alias for wine-validate'
    'run:run the selected project'
    'rn:alias for run'
    'package:package the selected project'
    'pkg:alias for package'
    'clean:clean generated project output'
    'cl:alias for clean'
    'inspect:inspect project type, runner, toolchain, tasks, and artifacts'
    'info:alias for inspect'
    'status:show resolved repository and runner status'
    'st:alias for status'
    'doctor:diagnose devtool and project prerequisites'
    'dr:alias for doctor'
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
    'inspect-artifact:inspect artifact metadata, inventory, validation, and diff'
    'ia:alias for inspect-artifact'
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
    'targets:list named targets'
    'tg:alias for targets'
    'logs:list, open, or tail devtool logs'
    'history:list structured run history'
    'rollback:rollback a recorded commit'
    'handoff:generate a repository handoff'
    'ai-prompt:generate an implementation prompt'
    'ap:alias for ai-prompt'
    'completion:print shell completion'
    'hud:run a live HUD renderer'
    'android-hud-bridge:bridge Termux or chroot progress to Android'
    'termux-hud-bridge:alias for android-hud-bridge'
    'control-center:launch the Qt 6 Control Center'
    'gui:alias for control-center'
    'ipc-server:run the Control Center IPC broker'
  )
  _describe -t commands 'devtool command' commands
}

_devtool_test_args() {
  _arguments \
    '--no-build[do not rebuild before dotnet test]' \
    '(--list --list-tests)'{--list,--list-tests}'[discover and list tests without executing]' \
    '--profile[saved test profile]:profile:_devtool_test_profiles' \
    '*--select[select a type, suite, tag, class, or name]:selector:' \
    '*--id[select an exact framework test ID]:test id:' \
    '*--tag[include a test tag or trait]:tag:' \
    '*--exclude-tag[exclude a test tag or trait]:tag:' \
    '*--path[select tests below a source path]:path:_files' \
    '*--class[select a test class]:class:' \
    '*--method[select a test method]:method:' \
    '--filter[framework-native test filter]:filter:' \
    '--task[Gradle test task override]:task:' \
    '--failed[rerun tests that failed in the previous structured run]' \
    '--changed[select tests related to changed files]' \
    '--changed-from[Git base for changed test selection]:revision:' \
    '--instrumented[run Android instrumentation tests on a device]'
}

_devtool_validation_args() {
  _arguments \
    '--skip-restore[skip dependency restore]' \
    '--skip-build[skip project build]' \
    '--skip-test[skip project tests]' \
    '--test-profile[saved test profile used by validation]:profile:_devtool_test_profiles' \
    '*--select[select tests by type, suite, tag, class, or name]:selector:' \
    '*--id[select an exact framework test ID]:test id:' \
    '*--tag[include a test tag or trait]:tag:' \
    '*--exclude-tag[exclude a test tag or trait]:tag:' \
    '*--path[select tests below a source path or package]:path:_files' \
    '*--class[select a test class]:class:' \
    '*--method[select a test method]:method:' \
    '--filter[framework-native test filter]:filter:' \
    '(--task --test-task)'{--task,--test-task}'[native test task/script/target override]:task:' \
    '--failed[rerun tests that failed in the previous structured run]' \
    '--changed[select tests related to changed files]' \
    '--changed-from[Git base for changed test selection]:revision:' \
    '--instrumented[run Android instrumentation tests on a device]' \
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
    '--next[run only the next pending phase]' \
    '--phase[run a named pending phase]:phase:(preflight dependencies compile resources unit-test package artifact-check lint instrumentation finalize)' \
    '--through[run pending phases through this phase]:phase:(preflight dependencies compile resources unit-test package artifact-check lint instrumentation finalize)' \
    '--complete[run all remaining phases]' \
    '--from-phase[start this invocation at a named phase]:phase:(preflight dependencies compile resources unit-test package artifact-check lint instrumentation finalize)' \
    '--pause-after[pause after a named phase]:phase:(preflight dependencies compile resources unit-test package artifact-check lint instrumentation finalize)' \
    '--max-phases[maximum phases for this invocation]:count:' \
    '--max-runtime[pause at a phase boundary after this duration]:duration:' \
    '--require-complete[return failure when the invocation pauses incomplete]' \
    '--stop-below-battery[pause below this battery percentage]:percent:' \
    '--require-charging[pause unless the Android device is charging]' \
    '--stop-above-temperature[pause above this device temperature]:celsius:' \
    '(--stop-gradle-after-phase --keep-gradle-daemon)--stop-gradle-after-phase[stop Gradle daemons after each phase]' \
    '(--stop-gradle-after-phase --keep-gradle-daemon)--keep-gradle-daemon[keep Gradle daemons between phase invocations]' \
    '--session[specific validation session ID]:session:' \
    '--keep-changes[keep repository changes when aborting]' \
    '1:validation action or artifact scope:(start next resume status finalize abort overlay overlays)' \
    '2:artifact:_devtool_artifact_path'
}

_devtool_apply_args() {
  _arguments \
    '--validate[run validation after applying]' \
    '--no-validate[disable validation requested by the artifact manifest]' \
    '--test-profile[saved test profile for artifact validation]:profile:_devtool_test_profiles' \
    '*--select[select tests by type, suite, tag, class, or name]:selector:' \
    '*--id[select an exact framework test ID]:test id:' \
    '*--tag[include a test tag or trait]:tag:' \
    '*--exclude-tag[exclude a test tag or trait]:tag:' \
    '*--path[select tests below a source path or package]:path:_files' \
    '*--class[select a test class]:class:' \
    '*--method[select a test method]:method:' \
    '--filter[framework-native test filter]:filter:' \
    '(--task --test-task)'{--task,--test-task}'[native test task/script/target override]:task:' \
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
    '--next[run only the next validation phase]' \
    '--phase[run a named validation phase]:phase:(preflight dependencies compile resources unit-test package artifact-check lint instrumentation finalize)' \
    '--through[run validation through this phase]:phase:(preflight dependencies compile resources unit-test package artifact-check lint instrumentation finalize)' \
    '--complete[run all remaining validation phases]' \
    '--pause-after[pause after a named validation phase]:phase:(preflight dependencies compile resources unit-test package artifact-check lint instrumentation finalize)' \
    '--max-phases[maximum phases for this invocation]:count:' \
    '--max-runtime[pause at a phase boundary after this duration]:duration:' \
    '--require-complete[fail when validation pauses incomplete]' \
    '--stop-below-battery[pause below this battery percentage]:percent:' \
    '--require-charging[pause unless charging]' \
    '--stop-above-temperature[pause above this temperature]:celsius:' \
    '(--stop-gradle-after-phase --keep-gradle-daemon)--stop-gradle-after-phase[stop Gradle daemons after each phase]' \
    '(--stop-gradle-after-phase --keep-gradle-daemon)--keep-gradle-daemon[keep Gradle daemons between phases]' \
    '--session[specific validation session ID]:session:' \
    '--commit[commit after successful apply and validation]:message:' \
    '--no-commit[disable a commit requested by the artifact manifest]' \
    '--no-artifact-script[skip embedded artifact validation scripts]' \
    '*:artifact:_devtool_artifact_path'
}

_devtool() {
  local context state line
  typeset -A opt_args

  _arguments -C \
    '(-r --repo)'{-r,--repo}'[repository root]:repo:_devtool_repo_path' \
    '(-c --config)'{-c,--config}'[configuration file]:config:_files' \
    '--target[named target/profile from .devtool.toml]:target:_devtool_targets' \
    '--variant[build/test variant within the selected target]:variant:_devtool_variants' \
    '--solution[exact solution path for restore/build/test]:solution:_files' \
    '--project[exact project path for run/validation]:project:_files' \
    '--project-type[override project detection]:type:(auto dotnet android gradle python node rust go maven cmake meson make command unknown)' \
    '(-v --verbose)'{-v,--verbose}'[stream full command output]' \
    '(-y --yes)'{-y,--yes}'[assume yes for confirmations]' \
    '(-f --force)'{-f,--force}'[force dangerous actions]' \
    '--dry-run[print commands without executing]' \
    '--no-build[for test, do not rebuild before dotnet test]' \
    '(-k --continue-on-error)'{-k,--continue-on-error}'[continue with the next independent step after an error]' \
    '--fail-fast[stop the batch at the first failed step]' \
    '--performance[write a JSON performance report]' \
    '--performance-file[performance report path]:file:_files' \
    '--machine-file[write the machine envelope]:file:_files' \
    '(-p --copy)'{-p,--copy}'[copy summary on success or verbose output on failure]' \
    '--no-clipboard[disable clipboard prompts and copy behavior]' \
    '--copy-via[preferred clipboard backend]:backend:(auto socket fifo termux x11 wayland)' \
    '--theme[summary theme]:theme:(auto plain)' \
    '--stdout-payload[select stdout content]:payload:(auto summary raw)' \
    '(--auto-hud --hud-auto --no-hud)'{--auto-hud,--hud-auto}'[auto-start or reuse the selected HUD]' \
    '(--auto-hud --hud-auto --no-hud)--no-hud[do not publish live HUD progress]' \
    '--hud-mode[HUD backend]:mode:(desktop android-bridge desktop-window gtk-window web studio terminal)' \
    '(--hud-overlay --hud-no-overlay)--hud-overlay[also show the run in the Android floating overlay]' \
    '(--hud-overlay --hud-no-overlay)--hud-no-overlay[keep the run in the app without opening the overlay]' \
    '(--host --chroot --execution-environment)--host[force the current host environment]' \
    '(--host --chroot --execution-environment)--chroot[run command phases in a named chroot]:name:' \
    '(--host --chroot --execution-environment)--execution-environment[execution backend]:backend:(auto native-termux chroot desktop-linux android-device external-device)' \
    '--no-auto-chroot[disable automatic Termux-to-chroot routing]' \
    '1:command:->command' \
    '*::argument:->args'

  case $state in
    command)
      _devtool_commands
      ;;
    args)
      case $line[1] in
        test|t)
          _devtool_test_args
          ;;
        build|b)
          _arguments \
            '(-o --artifact-output-dir --output-dir)'{-o,--artifact-output-dir,--output-dir}'[copy produced Android/Gradle artifacts here]:directory:_files -/' \
            '(-n --artifact-output-name --output-name)'{-n,--artifact-output-name,--output-name}'[base filename for copied Android/Gradle artifacts]:name:' \
            '*:runner args:'
          ;;
        validate|v|start-validation|sv|resume-validation|rv)
          _devtool_validation_args
          ;;
        validation-status|vs|validation-finalize|vf)
          _arguments '--session[specific validation session ID]:session:'
          ;;
        validation-abort|va)
          _arguments '--session[specific validation session ID]:session:' '--keep-changes[keep repository changes instead of rolling back]'
          ;;
        wine-validate|wv)
          _arguments \
            '--skip-restore[skip dependency restore]' \
            '--skip-build[skip project build]' \
            '--skip-test[skip project tests]' \
            '--test-profile[saved test profile used by validation]:profile:_devtool_test_profiles' \
            '--watch[rerun validation when files change]' \
            '--watch-interval[seconds between watch polls]:seconds:' \
            '--matrix[comma-separated runtime IDs, target names, or variants]:matrix:' \
            '--matrix-mode[matrix interpretation]:mode:(rid target variant)'
          ;;
        doctor|dr)
          _arguments '--fix[create safe devtool state and cache directories]'
          ;;
        clean|cl)
          _arguments '--caches[also prune devtool-managed caches and transient state]'
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
        inspect-artifact|ia)
          _arguments \
            '--diff[include a repository-relative unified diff]' \
            '--diff-context[number of context lines]:lines:' \
            '--max-diff-lines[maximum emitted diff lines]:lines:' \
            '1:artifact:_devtool_artifact_path'
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
            '--project-type[project type]:type:(auto dotnet android gradle python node rust go maven cmake meson make command unknown)' \
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
        config)
          if (( ${#line} < 2 )); then
            _values 'config action' path show defaults init edit resolve doctor
          else
            case $line[2] in
              path|show|defaults|doctor)
                _arguments '--global[use the user global config]' '--minimal[prefer sparse minimal defaults]'
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
        handoff)
          _arguments '(-o --output)'{-o,--output}'[write handoff to file]:file:_files'
          ;;
        hud)
          _arguments \
            '--state-dir[HUD state directory]:directory:_files -/' \
            '--mode[HUD renderer]:mode:(auto android-gui android-bridge termux-gui terminal web studio desktop desktop-window gtk-window desktop-notify v3 v4 v5 v6 console dashboard all unix gnome)' \
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
          _arguments '--last[rollback the last recorded commit]' '1:commit:'
          ;;
        control-center|gui)
          _arguments '--binary[override the Control Center executable]:file:_files'
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
