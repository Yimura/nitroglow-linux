// Walk callers of the three ADL i2c primitives and dump decompiled C.
import ghidra.app.script.GhidraScript;
import ghidra.app.decompiler.DecompInterface;
import ghidra.app.decompiler.DecompileResults;
import ghidra.program.model.address.Address;
import ghidra.program.model.listing.Function;
import java.util.*;

public class DumpCallers extends GhidraScript {

    private static final String[] SEEDS = { "00431fa0", "004320e0", "00431e80" };
    private static final int MAX_DEPTH = 3;
    private static final int MAX_FUNCS = 220;

    public void run() throws Exception {
        DecompInterface decomp = new DecompInterface();
        decomp.openProgram(currentProgram);

        Map<Function, Integer> depth = new LinkedHashMap<>();
        Deque<Function> queue = new ArrayDeque<>();

        for (String s : SEEDS) {
            Address a = currentProgram.getAddressFactory().getAddress(s);
            Function f = getFunctionAt(a);
            if (f == null) { println("!! no function at " + s); continue; }
            depth.put(f, 0);
            queue.add(f);
        }

        while (!queue.isEmpty() && depth.size() < MAX_FUNCS) {
            Function f = queue.poll();
            int d = depth.get(f);
            if (d >= MAX_DEPTH) continue;
            for (Function c : f.getCallingFunctions(monitor)) {
                if (!depth.containsKey(c)) {
                    depth.put(c, d + 1);
                    queue.add(c);
                }
            }
        }

        println("=== TOTAL " + depth.size() + " FUNCTIONS ===");
        for (Map.Entry<Function, Integer> e : depth.entrySet()) {
            Function f = e.getKey();
            println("");
            println("//###### depth=" + e.getValue() + "  " + f.getEntryPoint() + "  " + f.getName());
            DecompileResults res = decomp.decompileFunction(f, 90, monitor);
            if (res.decompileCompleted() && res.getDecompiledFunction() != null) {
                println(res.getDecompiledFunction().getC());
            } else {
                println("// decompile failed: " + res.getErrorMessage());
            }
        }
        decomp.dispose();
    }
}
