package io.github.mikeceead.devtoolhud;

import java.util.ArrayList;
import java.util.List;

final class ValidationScope {
    static final String NEXT = "next";
    static final String THROUGH = "through";
    static final String SELECTED = "selected";
    static final String REMAINING = "remaining";

    String mode = NEXT;
    String throughPhase = "";
    final List<String> selectedPhases = new ArrayList<>();

    String summary() {
        if (THROUGH.equals(mode)) return "Through " + human(throughPhase);
        if (SELECTED.equals(mode)) return selectedPhases.size() + " selected phase" + (selectedPhases.size() == 1 ? "" : "s");
        if (REMAINING.equals(mode)) return "All remaining phases";
        return "Next eligible phase";
    }

    static boolean supported(String value) {
        return NEXT.equals(value) || THROUGH.equals(value) || SELECTED.equals(value) || REMAINING.equals(value);
    }

    private static String human(String value) {
        String clean = value == null ? "" : value.trim().replace('-', ' ');
        if (clean.isEmpty()) return "phase";
        return Character.toUpperCase(clean.charAt(0)) + clean.substring(1);
    }
}
