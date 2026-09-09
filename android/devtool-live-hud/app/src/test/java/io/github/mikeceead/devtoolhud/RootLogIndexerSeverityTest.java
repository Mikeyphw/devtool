package io.github.mikeceead.devtoolhud;

import org.junit.Test;

import static org.junit.Assert.assertEquals;

public final class RootLogIndexerSeverityTest {
    @Test
    public void warningPrefixWinsOverQuotedErrorText() {
        assertEquals("warning", RootLogIndexer.classifySeverity(
                "warning: ADB probe failed via adb: error: protocol fault"));
    }

    @Test
    public void incidentalFailureAndErrorVocabularyDoesNotFailSuccessfulLogs() {
        assertEquals("info", RootLogIndexer.classifySeverity("failure-mode=fail-fast"));
        assertEquals("info", RootLogIndexer.classifySeverity("typed-ui-error"));
        assertEquals("success", RootLogIndexer.classifySeverity(
                "Phase 57 runtime failure recovery UX validator passed"));
        assertEquals("success", RootLogIndexer.classifySeverity(
                "TestFrozenReferenceOracleCLI/typed-ui-error passed"));
    }

    @Test
    public void structuredFailuresStillClassifyAsErrors() {
        assertEquals("error", RootLogIndexer.classifySeverity(
                "e: file:///repo/App.kt:12: Unresolved reference 'Thing'"));
        assertEquals("error", RootLogIndexer.classifySeverity(
                "Execution failed for task ':app:compileDebugKotlin'."));
        assertEquals("error", RootLogIndexer.classifySeverity(
                "23 errors, 4 warnings"));
        assertEquals("fatal", RootLogIndexer.classifySeverity(
                "java.lang.UnsatisfiedLinkError: missing symbol"));
    }

    @Test
    public void zeroErrorSummariesDoNotCreateFailures() {
        assertEquals("warning", RootLogIndexer.classifySeverity(
                "Diagnostics: 4 warnings, 0 errors"));
        assertEquals("success", RootLogIndexer.classifySeverity(
                "Tests: 41 passed, 0 failed, 0 skipped"));
    }
}
