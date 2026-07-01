from __future__ import annotations

ZSH_COMPLETION = r"""#compdef devtool

_devtool_steps() {
  local -a steps
  steps=(
    'restore:restore dependencies'
    'build:build project'
    'test:run tests'
    'validate:restore+build+test'
    'wine-validate:validate with wine env'
    'run:run project'
    'package:package project'
    'apply-overlay=:apply overlay artifact'
    'apply-snapshot=:apply snapshot artifact'
    'commit=:git commit message'
    'tag=:create git tag'
  )
  _describe -t steps 'batch step' steps
}

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
    selected=$(find ~/Downloads . -maxdepth 4 \( -name '*.zip' -o -name '*.tar' -o -name '*.tar.gz' -o -name '*.tgz' \) 2>/dev/null | fzf --prompt='artifact> ' --height=40% --reverse) || return 1
    compadd -- "$selected"
  else
    _files
  fi
}

_devtool() {
  local context state line
  typeset -A opt_args

  _arguments -C \
    '(-r --repo)'{-r,--repo}'[repository root]:repo:_devtool_repo_path' \
    '(-c --config)'{-c,--config}'[config path]:config:_files' \
    '--target[named target/profile from .devtool.toml]:target:' \
    '--solution[exact solution path]:solution:_files' \
    '--project[exact project path]:project:_files' \
    '(-v --verbose)'{-v,--verbose}'[stream full output]' \
    '(-j --json)'{-j,--json}'[emit compact machine envelope]' \
    '--machine[emit machine envelope on stdout]:(compact pretty)' \
    '--machine-file[write machine envelope to file]:file:_files' \
    '(-y --yes)'{-y,--yes}'[assume yes for confirmations]' \
    '(-f --force)'{-f,--force}'[force dangerous actions]' \
    '--no-build[for test: pass --no-build to dotnet test]' \
    '(-n --notify)'{-n,--notify}'[send notification]' \
    '--notify-brief[send brief notification]' \
    '--notify-on-success[only notify on success]' \
    '--notify-on-fail[only notify on failure]' \
    '--notify-progress[update live Termux notification]' \
    '--notify-id[notification id]:id:' \
    '--tasker[enable Tasker JSON/broadcast/progress integration]' \
    '--tasker-progress[write live Tasker JSON files]' \
    '--tasker-dir[Tasker JSON directory]:dir:_files -/' \
    '--tasker-broadcast[send Android broadcast for Tasker events]' \
    '--tasker-action[Android broadcast action]:action:' \
    '--copy[always copy raw output]' \
    '--copy-summary[always copy rendered summary]' \
    '--copy-json[always copy JSON summary]' \
    '--copy-via[clipboard backend]:(auto socket fifo termux x11 wayland)' \
    '--no-clipboard[never prompt for clipboard]' \
    '--dry-run[print commands without running]' \
    '--continue-on-error[continue batch after errors]' \
    '--fail-fast[stop batch on first error]' \
    '--host[force host execution]' \
    '--chroot[run command phases inside chroot via chrootctl]:name:' \
    '--stdout-payload[select stdout payload]:(auto summary json raw)' \
    '--save-json-payload[write machine JSON payload]:file:_files' \
    '1:command:(restore build test validate wine-validate run package commit apply-overlay apply-snapshot batch logs status doctor config init-config completion tag release-notes ai-prompt detect handoff history rollback targets v wv b t ao as)' \
    '*::arg:->args'

  case $words[1] in
    test|t)
      _arguments '--no-build[pass --no-build to dotnet test]'
      ;;
    validate|v|wine-validate|wv)
      _arguments \
        '--skip-restore[skip restore during validation]' \
        '--skip-build[skip build during validation]' \
        '--skip-test[skip test during validation]' \
        '--watch[rerun validation when files change]' \
        '--watch-interval[seconds between watch polls]:seconds:' \
        '--matrix[comma-separated runtime IDs or targets]:matrix:' \
        '--matrix-mode[matrix interpretation]:(rid target)'
      ;;
    apply-overlay|ao|apply-snapshot|as)
      _arguments \
        '--validate[run validation after apply]' \
        '--validate-only[skip apply and only run validation]' \
        '--preview[preview archive changes without applying]' \
        '--diff[show repo-relative unified diff without applying]' \
        '--interactive[show preview/diff and ask before applying]' \
        '--no-manifest-check[disable artifact manifest validation]' \
        '--skip-restore[skip restore during validation]' \
        '--skip-build[skip build during validation]' \
        '--skip-test[skip test during validation]' \
        '--commit[commit with this message after successful apply and validation]:message:' \
        '1:artifact:_devtool_artifact_path'
      ;;
    batch)
      _devtool_steps
      ;;
    config)
      _values 'config subcommand' show defaults resolve doctor
      ;;
    completion)
      _values 'shell' zsh
      ;;
    detect)
      _arguments '--format[output format]:(text json)'
      ;;
    handoff)
      _arguments '(-o --output)'{-o,--output}'[write handoff to file]:file:_files'
      ;;
    history)
      _arguments '--limit[number of entries]:limit:'
      ;;
    rollback)
      _arguments '--last[rollback last recorded commit]' '1:commit:'
      ;;
    ai-prompt|ap)
      _arguments \
        '--repo-name[repo name]:name:' \
        '--project-type[project type]:(auto dotnet python unknown)' \
        '--build-target[build target]:path:_files' \
        '--run-target[run target]:path:_files' \
        '--target-name[named target/profile]:target:' \
        '--solution-target[solution target]:solution:_files' \
        '--delivery-mode[delivery mode]:(overlay snapshot)' \
        '--artifact-type[artifact type]:(zip tar)' \
        '--overlay-count[successful overlay count]:count:' \
        '--commit-message[default commit message]:message:' \
        '--wine[prefer wine validate wording]' \
        '(-o --output)'{-o,--output}'[write prompt to file]:file:_files' \
        '--format[output format]:(markdown text)'
      ;;
    *)
      ;;
  esac
}

_devtool "$@"
"""
