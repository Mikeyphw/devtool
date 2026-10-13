public final class G5Smoke {
    private G5Smoke() {}
    public static void main(String[] args) {
        if (2 + 3 != 5) throw new AssertionError("JVM smoke failed");
        System.out.println("G5 Java fixture passed");
    }
}
