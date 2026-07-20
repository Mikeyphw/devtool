package io.github.mikeceead.devtoolhud;

final class GitCommitDetail {
    String hash = "";
    String text = "";
    String error = "";
    boolean success() { return error.isEmpty(); }
}
