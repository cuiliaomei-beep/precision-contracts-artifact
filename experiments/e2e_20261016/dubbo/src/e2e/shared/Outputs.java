package e2e.shared;
public final class Outputs {
    private Outputs() {}
    public static void sink(String id, String value) {
        System.out.println("E2E_SINK\t" + id + "\t" + value);
    }
}
