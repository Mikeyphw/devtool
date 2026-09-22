package io.github.mikeceead.devtoolhud;

import org.junit.Test;

import static org.junit.Assert.*;

public class ExecutionGraphStateTest {
    private static final String FAILED_GRAPH = "{"
            + "\"schema_version\":3,\"run_id\":\"run-10\",\"last_sequence\":12,\"last_event_id\":\"evt-12\","
            + "\"status\":\"failed\",\"graph_sealed\":true,"
            + "\"accounting\":{\"leaf\":{\"total\":3,\"succeeded\":2,\"failed\":1,\"blocked\":0},"
            + "\"leaf_measured_progress\":{\"percent\":43.0,\"coverage_percent\":100.0,\"determinate\":true},"
            + "\"leaf_resolution_percent\":100.0},"
            + "\"nodes\":["
            + "{\"node_id\":\"build\",\"parent_id\":\"\",\"label\":\"Build\",\"kind\":\"workflow\",\"status\":\"failed\",\"progress\":{\"percent\":43}},"
            + "{\"node_id\":\"download\",\"parent_id\":\"build\",\"label\":\"Download SDK\",\"kind\":\"download\",\"status\":\"succeeded\",\"progress\":{\"percent\":100}},"
            + "{\"node_id\":\"compile\",\"parent_id\":\"build\",\"label\":\"Compile\",\"kind\":\"gradle-task\",\"status\":\"failed\",\"progress\":{\"percent\":29,\"detail\":\"javac\"},\"diagnostic_ids\":[\"d1\"],\"process_ids\":[\"p1\"]},"
            + "{\"node_id\":\"zip\",\"parent_id\":\"build\",\"label\":\"Create ZIP\",\"kind\":\"artifact\",\"status\":\"succeeded\",\"progress\":{\"percent\":100}}],"
            + "\"diagnostics\":[{\"diagnostic_id\":\"d1\",\"node_id\":\"compile\",\"process_id\":\"p1\",\"severity\":\"error\",\"code\":\"JAVAC\",\"message\":\"Compilation failed\",\"log_path\":\"/tmp/build.log\",\"evidence\":[\"/tmp/build.log\"]}]"
            + "}";

    @Test public void parsesCanonicalAccountingWithoutCompletingFailedWork() {
        ExecutionGraphState state = ExecutionGraphState.parse(FAILED_GRAPH);
        assertTrue(state.available());
        assertEquals("failed", state.status);
        assertEquals(43, state.measuredPercent);
        assertEquals(100, state.resolutionPercent);
        assertEquals(3, state.totalOperations);
        assertEquals(2, state.succeededOperations);
        assertEquals(1, state.failedOperations);
        assertEquals("Compilation failed", state.primaryFailureMessage());
    }

    @Test public void exposesTypedChildOperationsWithStableIds() {
        ExecutionGraphState state = ExecutionGraphState.parse(FAILED_GRAPH);
        assertEquals(3, state.steps().size());
        assertEquals("download", state.steps().get(0).key);
        assertEquals("compile", state.steps().get(1).key);
        assertEquals("failed", state.steps().get(1).status);
        assertEquals(29, state.steps().get(1).progress);
        assertEquals("zip", state.steps().get(2).key);
    }

    @Test public void malformedGraphFailsClosedToUnavailable() {
        ExecutionGraphState state = ExecutionGraphState.parse("{");
        assertFalse(state.available());
        assertEquals("waiting", state.status);
    }
}
