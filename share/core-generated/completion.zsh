#compdef devtool
# Generated from the CORE-X04 command registry.
_devtool_core_generated() {
  local -a root_commands; root_commands=(actions adopt-batch affected ai-prompt android android-hud-bridge apply artifact artifacts audit batch bootstrap build bundle campaign clean cleanup commit compatibility completion completion-state config control-center core delivery detect doctor evidence federation final-seal git gradle groups handoff history hud init-config inspect inspect-artifact instrumentation invoke ipc-server jobs lint logs menu migration oci overlay package plan plugin preflight pytest release release-notes repo-pack restore resume-validation rollback run runs signing-identity ssh start-validation status tag target-local-jobs targets termux test transaction tui ui-contract validate validate-all validate-overlay validate-overlays validation-abort validation-accept-plan-change validation-events validation-finalize validation-menu validation-pause validation-plan validation-recover validation-report validation-reset validation-status validation-stop variants wine-validate workflows wrapper)
  if (( CURRENT == 2 )); then
    _describe 'devtool command' root_commands
    return
  fi
  _files
}
compdef _devtool_core_generated devtool
