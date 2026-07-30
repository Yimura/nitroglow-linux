// Dump decompiled C for a list of functions given by address, plus callees to a depth.
// Args: depth addr1 addr2 ...
import ghidra.app.script.GhidraScript;
import ghidra.app.decompiler.DecompInterface;
import ghidra.app.decompiler.DecompileResults;
import ghidra.program.model.address.Address;
import ghidra.program.model.listing.Function;
import java.util.*;

public class DumpFuncs extends GhidraScript {
    private DecompInterface decomp;

    public void run() throws Exception {
        String[] args = getScriptArgs();
        int depth = Integer.parseInt(args[0]);
        decomp = new DecompInterface();
        decomp.openProgram(currentProgram);

        LinkedHashSet<Function> work = new LinkedHashSet<>();
        for (int i = 1; i < args.length; i++) {
            Address a = currentProgram.getAddressFactory().getAddress(args[i]);
            Function f = getFunctionContaining(a);
            if (f == null) f = getFunctionAt(a);
            if (f == null) { println("!! no function at " + args[i]); continue; }
            work.add(f);
        }
        LinkedHashSet<Function> all = new LinkedHashSet<>(work);
        Set<Function> frontier = new LinkedHashSet<>(work);
        for (int d = 0; d < depth; d++) {
            Set<Function> next = new LinkedHashSet<>();
            for (Function f : frontier) {
                for (Function c : f.getCalledFunctions(monitor)) {
                    if (c.isThunk() || c.isExternal()) { all.add(c); continue; }
                    if (all.add(c)) next.add(c);
                }
            }
            frontier = next;
        }
        println("=== TOTAL " + all.size() + " functions ===");
        for (Function f : all) {
            println("\n\n########## " + f.getEntryPoint() + " " + f.getName()
                    + "  (" + (f.isThunk() ? "THUNK->" + f.getThunkedFunction(true) : "body") + ")");
            if (f.isExternal()) { println("  [external]"); continue; }
            if (f.isThunk()) { continue; }
            try {
                DecompileResults r = decomp.decompileFunction(f, 90, monitor);
                if (r != null && r.getDecompiledFunction() != null)
                    println(r.getDecompiledFunction().getC());
                else println("  [decompile failed]");
            } catch (Exception e) { println("  [ex " + e + "]"); }
        }
    }
}
