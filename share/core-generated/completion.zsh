#compdef devtool
# Generated from the CORE-X04 command registry.
_devtool_core_generated() {
  local -a root_commands; root_commands=(aff affected ai-prompt android android-hud-bridge ao aos ap apply apply-overlay apply-overlays apply-snapshot artifact as audit b batch bootstrap build bundle campaign ci cl clean cleanup commit compat compatibility completion completion-state config control-center core delivery detect doctor dr fed federation final-seal git gr gradle groups gui handoff history hud ia info init init-config inspect inspect-artifact instrumentation invoke ipc-server jb jobs lint logs menu oci overlay package pf pkg pl plan plugin plugins preflight pytest registry rel release release-notes repo-pack restore resume-validation rn rollback rs run runs rv seal signing-identity ssh st start-validation status sv t tag target-local-jobs targets termux termux-hud-bridge test tg tlj tui ui-contract uic v va validate validate-all validate-overlay validate-overlays validation-abort validation-accept-plan-change validation-events validation-finalize validation-menu validation-pause validation-plan validation-recover validation-report validation-reset validation-status validation-stop vapc variants vevents vf vm vo vos vpause vplan vrec vreport vreset vs vstop wf wine-validate workflows wrapper wv)
  if (( CURRENT == 2 )); then
    _describe 'devtool command' root_commands
    return
  fi
  _files
}
compdef _devtool_core_generated devtool
