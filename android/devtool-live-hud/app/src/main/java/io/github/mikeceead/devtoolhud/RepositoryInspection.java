package io.github.mikeceead.devtoolhud;

final class RepositoryInspection {
    boolean success;
    String gitRoot = "";
    String branch = "";
    String upstream = "";
    String remote = "";
    String status = "missing";
    String projectType = "auto";
    String error = "";
    boolean dirty;
    int ahead;
    int behind;
}
