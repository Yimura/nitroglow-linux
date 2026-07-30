// Find the native<->managed i2c bridge: callers of the i2c object ctors
// (FUN_00431e20 / FUN_00431e50) and anything that stores to obj+0x18.
import ghidra.app.script.GhidraScript;
import ghidra.app.decompiler.DecompInterface;
import ghidra.app.decompiler.DecompileResults;
import ghidra.program.model.address.Address;
import ghidra.program.model.listing.Function;
import java.util.*;

public class DumpBridge extends GhidraScript {
    public void run() throws Exception {
        DecompInterface decomp = new DecompInterface();
        decomp.openProgram(currentProgram);
        String[] ctors = {"00431e20", "00431e50"};
        LinkedHashSet<Function> callers = new LinkedHashSet<>();
        for (String s : ctors) {
            Function f = getFunctionAt(currentProgram.getAddressFactory().getAddress(s));
            if (f == null) { println("!! no fn at " + s); continue; }
            for (Function c : f.getCallingFunctions(monitor)) callers.add(c);
        }
        println("=== " + callers.size() + " callers of i2c ctors ===");
        for (Function f : callers) println("  " + f.getEntryPoint() + " " + f.getName());
        for (Function f : callers) {
            println("\n\n########## " + f.getEntryPoint() + " " + f.getName());
            try {
                DecompileResults r = decomp.decompileFunction(f, 120, monitor);
                if (r != null && r.getDecompiledFunction() != null) println(r.getDecompiledFunction().getC());
                else println("  [decompile failed]");
            } catch (Exception e) { println("  [ex " + e + "]"); }
        }
    }
}
